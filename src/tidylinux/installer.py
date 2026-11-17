"""Transactional user-level install, launch and ownership-checked removal."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from .archive import Limits, check_cancel, extract_archive, icon_priority, inspect_archive
from .appimage import install_icon
from .desktop import desktop_content, digest, verify_desktop
from .models import AppRecord, Inspection, TidyError
from .registry import Paths, Registry


def fsync_file(path: Path, content: bytes):
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Installer:
    def __init__(self, paths: Paths | None = None, limits: Limits = Limits()):
        self.paths = paths or Paths.default()
        self.registry = Registry(self.paths)
        self.limits = limits

    def install(self, inspection: Inspection, executable: str, name: str, version: str = "",
                cancel: Event | None = None) -> AppRecord:
        name, version = name.strip(), version.strip()
        if not name or len(name) > 200 or len(version) > 100 or any(ord(c) < 32 for c in name + version):
            raise TidyError("Enter a name (up to 200 characters) and a valid optional version.")
        if executable not in {c.path for c in inspection.candidates}:
            raise TidyError("Select an executable from the inspected archive.")
        with self.paths.locked():
            app_id = "tidylinux-" + uuid.uuid4().hex
            final = self.paths.apps / app_id
            desktop = self.paths.desktops / f"{app_id}.desktop"
            if final.exists() or final.is_symlink() or desktop.exists() or desktop.is_symlink():
                raise TidyError("Application ID collision. Please try the installation again.")
            with tempfile.TemporaryDirectory(prefix="install-", dir=self.paths.staging) as temp:
                staging = Path(temp)
                published = False
                desktop_published = False
                try:
                    snapshot = staging / "archive"
                    checksum = hashlib.sha256()
                    copied = 0
                    with inspection.source.open("rb") as source, snapshot.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            check_cancel(cancel)
                            copied += len(chunk)
                            if copied > self.limits.archive_bytes:
                                raise TidyError("Archive exceeds the compressed size limit.")
                            checksum.update(chunk)
                            output.write(chunk)
                    if checksum.hexdigest() != inspection.checksum:
                        raise TidyError("Archive changed since inspection. Please select it again.")
                    reviewed = inspect_archive(snapshot, self.limits, cancel)
                    # Avoid installing different contents after a source change.
                    if (reviewed.archive_size, reviewed.expanded_size, reviewed.candidates, reviewed.entries, reviewed.format) != (
                            inspection.archive_size, inspection.expanded_size, inspection.candidates, inspection.entries, inspection.format):
                        raise TidyError("Archive changed since inspection. Please select it again.")
                    bundle = staging / "bundle"
                    bundle.mkdir(mode=0o700)
                    payload = bundle / "payload"
                    payload.mkdir(mode=0o700)
                    extract_archive(snapshot, payload, self.limits, cancel)
                    launcher = payload / executable
                    if not launcher.is_file() or launcher.is_symlink():
                        raise TidyError("Selected executable is missing.")
                    launcher.chmod(0o700)
                    icon_path = min(reviewed.icons, key=lambda p: icon_priority(p, executable)) if reviewed.icons else None
                    installed_size = reviewed.expanded_size
                    if reviewed.format == "appimage":
                        icon_path = install_icon(snapshot, payload, cancel)
                        if icon_path:
                            installed_size += (payload / icon_path).stat().st_size
                            if installed_size > self.limits.expanded_bytes:
                                raise TidyError("AppImage and icon exceed the installation size limit.")
                    icon = str(final / "payload" / icon_path) if icon_path else None
                    record = AppRecord(app_id, name, version, str(final / "payload"), executable, icon,
                                       str(inspection.source), copied, checksum.hexdigest(),
                                       datetime.now(timezone.utc).isoformat(), str(desktop), "", installed_size)
                    content = desktop_content(record).encode("utf-8")
                    record = replace(record, desktop_checksum=digest(content))
                    fsync_file(bundle / "manifest.json", (json.dumps(record.to_dict(), indent=2) + "\n").encode())
                    desktop_stage = staging / "launcher.desktop"
                    fsync_file(desktop_stage, content)
                    sync_directory(bundle)
                    check_cancel(cancel)
                    # rename is atomic; exclusive desktop hard link cannot overwrite a file.
                    bundle.rename(final)
                    published = True
                    os.link(desktop_stage, desktop)
                    desktop_published = True
                    sync_directory(self.paths.apps)
                    sync_directory(self.paths.desktops)
                    return record
                except BaseException:
                    if desktop_published:
                        desktop.unlink(missing_ok=True)
                    if published:
                        shutil.rmtree(final)
                    raise

    def launch(self, record: AppRecord) -> subprocess.Popen:
        with self.paths.locked():
            current = self.registry.load(self.paths.apps / record.app_id)
            executable = Path(current.install_dir) / current.executable
            try:
                if not executable.is_file() or executable.is_symlink() or not executable.resolve().is_relative_to(Path(current.install_dir).resolve()):
                    raise TidyError("Executable is missing or unsafe. Reinstall the application.")
                process = subprocess.Popen([str(executable)], cwd=executable.parent,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
                # Reap children without a persistent service or idle polling.
                from threading import Thread
                Thread(target=process.wait, daemon=True).start()
                return process
            except OSError as exc:
                raise TidyError(f"Could not launch {current.name}: {exc}. The bundle may need additional system libraries.") from exc

    def uninstall(self, record: AppRecord):
        with self.paths.locked():
            directory = self.paths.apps / record.app_id
            current = self.registry.load(directory)
            if current != record:
                raise TidyError("Application record changed. Refresh before uninstalling.")
            verify_desktop(current)
            # A valid manifest binds removal to this exact namespaced directory.
            # rmtree does not follow links; reject swapped payload ancestors too.
            if directory.is_symlink() or (directory / "payload").is_symlink():
                raise TidyError("Managed directory has been replaced by a symbolic link.")
            desktop = Path(current.desktop_path)
            # Invalidate registration atomically before deleting owned files.
            tombstone = Path(tempfile.mkdtemp(prefix="remove-", dir=self.paths.staging))
            try:
                directory.rename(tombstone / "bundle")
                try:
                    desktop.unlink(missing_ok=True)
                except OSError:
                    (tombstone / "bundle").rename(directory)
                    raise
                shutil.rmtree(tombstone / "bundle")
            finally:
                if not any(tombstone.iterdir()):
                    tombstone.rmdir()


def native_trash(path: Path):
    # Gio is optional for backend-only tests; the desktop runtime supplies it.
    from gi.repository import Gio, GLib
    try:
        if not Gio.File.new_for_path(str(path)).trash(None):
            raise TidyError("The desktop could not move the archive to Trash. The archive was preserved.")
    except GLib.Error as exc:
        raise TidyError(f"The archive was preserved. Could not move it to Trash: {exc.message}") from exc


def trash_source(record: AppRecord):
    """Only call following explicit user confirmation; never permanently delete."""
    path = Path(record.source)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != record.source_size:
        raise TidyError("The original archive is missing or has changed; it was left untouched.")
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    if checksum.hexdigest() != record.checksum:
        raise TidyError("The archive has changed since installation; it was left untouched.")
    native_trash(path)
