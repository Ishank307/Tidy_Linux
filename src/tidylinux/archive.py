"""Bounded archive validation and extraction. Archive code never executes content.

Links are intentionally unsupported: rejecting all links is simpler to audit than
resolving arbitrary link graphs. Each extraction revalidates its private snapshot.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import gzip
import lzma
import struct
import stat
import tarfile
import zipfile
from threading import Event

from .models import Candidate, Cancelled, Inspection, TidyError


@dataclass(frozen=True)
class Limits:
    entries: int = 30_000
    expanded_bytes: int = 4 * 1024**3
    file_bytes: int = 2 * 1024**3
    archive_bytes: int = 4 * 1024**3
    ratio: int = 1000


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    mode: int
    directory: bool
    member: object


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("Operation cancelled.")


def safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 4096:
        raise TidyError("Archive contains an invalid path.")
    if name.startswith("/") or "\\" in name or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in name):
        raise TidyError(f"Unsafe archive path: {name!r}")
    parts = name.rstrip("/").split("/")
    # A common harmless ./ prefix is allowed; traversal is never allowed.
    if ".." in parts or any(len(part) > 255 for part in parts):
        raise TidyError(f"Unsafe archive path: {name!r}")
    result = str(PurePosixPath(name))
    if result in ("", ".") or PurePosixPath(result).is_absolute():
        raise TidyError(f"Unsafe archive path: {name!r}")
    return result


class ValidatedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        member = self
        metadata = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                    tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
        if member.type in metadata:
            if not 0 <= member.size <= 1024 * 1024:
                raise TidyError("Tar metadata record exceeds the 1 MiB limit.")
        elif member.type == tarfile.GNUTYPE_SPARSE or not (member.isfile() or member.isdir()):
            raise TidyError("Archive contains links, sparse files, or unsupported special files.")
        return super()._proc_member(archive)

    def _proc_gnusparse_00(self, *args):
        raise TidyError("Sparse archives are unsupported.")

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


class BoundedTarReader:
    """Bound hidden PAX/GNU metadata before tarfile allocates its payload."""
    def __init__(self, stream, limits):
        self.stream = stream
        self.budget = limits.expanded_bytes + limits.entries * 8192
        self.header_reads = 0
        self.max_headers = limits.entries * 4 + 16

    def read(self, size=-1):
        if size < 0 or size > 1024 * 1024:
            raise TidyError("Tar metadata record exceeds the 1 MiB limit.")
        if size == 512:
            self.header_reads += 1
            if self.header_reads > self.max_headers:
                raise TidyError("Archive contains too many metadata records.")
        if self.tell() + size > self.budget:
            raise TidyError("Tar stream exceeds its expanded size budget.")
        return self.stream.read(size)

    def tell(self):
        return self.stream.tell()

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.tell() + offset
        if whence not in (0, 1) or not 0 <= target <= self.budget:
            raise TidyError("Tar stream exceeds its expanded size budget.")
        return self.stream.seek(target)


def validate_zip_directory(path: Path, limits: Limits):
    """Count central headers before ZipFile eagerly constructs member objects.

    ZIP64 and multipart archives are outside V1; normal ZIP is limited to 4 GiB.
    """
    with path.open("rb") as stream:
        length = path.stat().st_size
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
        index = tail.rfind(b"PK\x05\x06")
        if index < 0 or len(tail) - index < 22:
            raise TidyError("ZIP end record is missing or corrupt.")
        _, disk, central_disk, on_disk, count, size, offset, comment = struct.unpack_from("<4s4H2LH", tail, index)
        if disk or central_disk or on_disk != count or count == 65535 or offset == 0xffffffff or size == 0xffffffff:
            raise TidyError("ZIP64 and multipart ZIP archives are unsupported.")
        if count > limits.entries or size > 64 * 1024**2:
            raise TidyError("ZIP contains too many entries or too much metadata.")
        if offset + size > length or len(tail) - index != 22 + comment:
            raise TidyError("ZIP central directory is invalid.")
        stream.seek(offset)
        end, actual = offset + size, 0
        while stream.tell() < end:
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise TidyError("ZIP central directory is invalid.")
            name, extra, note = struct.unpack_from("<3H", header, 28)
            actual += 1
            if actual > limits.entries or name > 4096 or extra > 8192 or note > 8192:
                raise TidyError("ZIP metadata exceeds safety limits.")
            stream.seek(name + extra + note, 1)
        if actual != count or stream.tell() != end:
            raise TidyError("ZIP entry count does not match its directory.")


@contextmanager
def open_archive(path: Path, limits: Limits):
    try:
        if path.stat().st_size > limits.archive_bytes:
            raise TidyError("Archive exceeds the compressed size limit.")
        if zipfile.is_zipfile(path):
            validate_zip_directory(path, limits)
            with zipfile.ZipFile(path) as archive:
                yield archive, "zip"
        else:
            # Explicit formats: uncompressed tar and bzip2 are outside V1.
            with path.open("rb") as stream:
                magic = stream.read(6)
            if not (magic.startswith(b"\x1f\x8b") or magic == b"\xfd7zXZ\x00"):
                raise TidyError("Choose an AppImage, gzip/xz tar archive or ZIP containing a portable Linux app.")
            opener = gzip.open if magic.startswith(b"\x1f\x8b") else lzma.open
            with opener(path, "rb") as decompressed:
                with tarfile.open(fileobj=BoundedTarReader(decompressed, limits), mode="r:", tarinfo=ValidatedTarInfo) as archive:
                    yield archive, "tar"
    except TidyError:
        raise
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError, RecursionError, lzma.LZMAError, struct.error) as exc:
        raise TidyError(f"Could not read archive: {exc}") from exc


def validated_entries(archive, kind: str, compressed_size: int, limits: Limits,
                      cancel: Event | None = None) -> list[Entry]:
    entries: list[Entry] = []
    seen: dict[str, bool] = {}
    total = 0
    members = archive.infolist() if kind == "zip" else archive
    for number, member in enumerate(members, 1):
        check_cancel(cancel)
        if number > limits.entries:
            raise TidyError("Archive contains too many entries.")
        raw = member.filename if kind == "zip" else member.name
        if raw.rstrip("/") in (".", "") and kind == "tar" and member.isdir():
            continue
        name = safe_relative(raw)
        if kind == "zip":
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            directory = member.is_dir()
            if member.flag_bits & 1:
                raise TidyError("Encrypted ZIP files are unsupported.")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or (file_type == stat.S_IFDIR and not directory):
                raise TidyError("Archive contains links or unsupported special files.")
            size = member.file_size
        else:
            if not (member.isfile() or member.isdir()) or member.issparse():
                raise TidyError("Archive contains links, sparse files, or unsupported special files.")
            directory, size, mode = member.isdir(), member.size, member.mode
        if name in seen:
            raise TidyError(f"Archive contains duplicate paths: {name}")
        if size < 0 or size > limits.file_bytes:
            raise TidyError("An archive member exceeds the file size limit.")
        total += size
        if total > limits.expanded_bytes or total > max(compressed_size, 1) * limits.ratio:
            raise TidyError("Archive exceeds expanded size or compression ratio limits.")
        seen[name] = directory
        entries.append(Entry(name, size, mode, directory, member))
    for name in seen:
        for parent in PurePosixPath(name).parents:
            if str(parent) in seen and not seen[str(parent)]:
                raise TidyError("Archive places files inside another file.")
    return entries


def member_stream(archive, kind: str, entry: Entry):
    return archive.open(entry.member) if kind == "zip" else archive.extractfile(entry.member)


def executable_kind(name: str, header: bytes, mode: int) -> str | None:
    base = PurePosixPath(name).name.lower()
    if base in {"install", "setup", "uninstall", "install.sh", "setup.sh", "uninstall.sh", "configure"}:
        return None
    if base.endswith(".so") or ".so." in base or base.endswith((".dll", ".exe", ".dylib")):
        return None
    if header.startswith(b"\x7fELF"):
        return "Linux executable"
    if header.startswith(b"#!") and ((mode & 0o111) or base.endswith((".sh", ".py"))):
        return "Script (review before running)"
    return None


def icon_priority(path: str, executable: str | set[str]):
    """Prefer app artwork over images belonging to bundled dependencies or docs."""
    image = PurePosixPath(path.lower())
    launchers = {PurePosixPath(executable.lower()).stem} if isinstance(executable, str) else executable
    unrelated = {"extensions", "plugins", "node_modules", "vendor", "docs", "doc", "test", "tests", "screenshots"}
    return (bool(unrelated.intersection(image.parts[:-1])),
            image.stem not in launchers,
            image.stem not in {"icon", "logo", "app", "application", "app-icon"},
            len(image.parts), path)


def appimage_type(header: bytes) -> int | None:
    # AppImageSpec: ELF executable with AI + format version at byte offset 8.
    if header.startswith(b"\x7fELF") and header[8:10] == b"AI":
        if len(header) < 64 or header[10] not in (1, 2):
            raise TidyError("Truncated or unsupported AppImage header. Choose a type 1 or type 2 AppImage.")
        return header[10]
    return None


def check_appimage_size(size: int, limits: Limits):
    if limits.entries < 1 or size > min(limits.archive_bytes, limits.file_bytes, limits.expanded_bytes):
        raise TidyError("AppImage exceeds the file size or installation limits.")


def inspect_archive(path: Path, limits: Limits = Limits(), cancel: Event | None = None) -> Inspection:
    path = path.expanduser().absolute()
    if not path.is_file():
        raise TidyError("Choose a regular local archive file.")
    checksum = hashlib.sha256()
    hashed_bytes = 0
    try:
        with path.open("rb") as source:
            header = source.read(64)
            source.seek(0)
            if path.stat().st_size > limits.archive_bytes:
                raise TidyError("Archive exceeds the compressed size limit.")
            while chunk := source.read(1024 * 1024):
                check_cancel(cancel)
                hashed_bytes += len(chunk)
                if hashed_bytes > limits.archive_bytes:
                    raise TidyError("Archive exceeds the compressed size limit.")
                checksum.update(chunk)
    except OSError as exc:
        raise TidyError(f"Could not read archive: {exc}") from exc
    image_type = appimage_type(header)
    if image_type is not None:
        check_appimage_size(hashed_bytes, limits)
        return Inspection(path, hashed_bytes, checksum.hexdigest(), hashed_bytes, 1,
                          (Candidate("application.AppImage", f"AppImage type {image_type}"),),
                          (), None, path.stem, "appimage")
    candidates, icons = [], []
    with open_archive(path, limits) as (archive, kind):
        size = path.stat().st_size
        entries = validated_entries(archive, kind, size, limits, cancel)
        for entry in entries:
            check_cancel(cancel)
            if entry.directory:
                continue
            with member_stream(archive, kind, entry) as stream:
                header = stream.read(256)
            executable = executable_kind(entry.name, header, entry.mode)
            if executable:
                candidates.append(Candidate(entry.name, executable))
            if entry.name.lower().endswith((".png", ".svg", ".xpm")) and entry.size <= 4 * 1024**2:
                icons.append(entry.name)
        if not candidates:
            raise TidyError("No portable Linux executable found. Traditional installers and setup scripts are not supported.")
        # Prefer launchers near the top over nested helper executables.
        candidates.sort(key=lambda c: (len(PurePosixPath(c.path).parts), c.path))
        launcher_names = {PurePosixPath(c.path.lower()).stem for c in candidates}
        icons.sort(key=lambda n: icon_priority(n, launcher_names))
        roots = {PurePosixPath(e.name).parts[0] for e in entries}
        top = next(iter(roots)) if len(roots) == 1 and all("/" in e.name or e.directory for e in entries) else None
        name = top or PurePosixPath(candidates[0].path).stem
        return Inspection(path, size, checksum.hexdigest(), sum(e.size for e in entries), len(entries),
                          tuple(candidates), tuple(icons), top, name)


def extract_archive(path: Path, destination: Path, limits: Limits = Limits(),
                    cancel: Event | None = None) -> None:
    """Destination must be a new private empty directory owned by the caller."""
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise TidyError("Extraction requires an empty private staging directory.")
    with path.open("rb") as source:
        image_type = appimage_type(source.read(64))
        if image_type is not None:
            size = path.stat().st_size
            check_appimage_size(size, limits)
            check_cancel(cancel)
            source.seek(0)
            target = destination / "application.AppImage"
            with target.open("xb") as output:
                copied = 0
                while chunk := source.read(1024 * 1024):
                    check_cancel(cancel)
                    copied += len(chunk)
                    check_appimage_size(copied, limits)
                    if copied > size:
                        raise TidyError("AppImage changed while copying.")
                    output.write(chunk)
                if copied != size:
                    raise TidyError("AppImage was truncated while copying.")
            target.chmod(0o700)
            return
    with open_archive(path, limits) as (archive, kind):
        entries = validated_entries(archive, kind, path.stat().st_size, limits, cancel)
        for entry in entries:
            check_cancel(cancel)
            target = destination / entry.name
            if kind == "tar" and hasattr(tarfile, "data_filter"):
                tarfile.data_filter(entry.member, str(destination))
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if entry.directory:
                target.mkdir(exist_ok=True, mode=0o700)
                continue
            with member_stream(archive, kind, entry) as source, target.open("xb") as output:
                remaining = entry.size
                while remaining:
                    check_cancel(cancel)
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise TidyError("Archive member is truncated.")
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise TidyError("Archive member exceeds its declared size.")
            target.chmod(0o700 if entry.mode & 0o111 else 0o600)
