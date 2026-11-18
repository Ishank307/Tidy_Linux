"""On-demand, bounded cleanup review. Never permanently deletes anything."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import stat

from .archive import check_cancel
from .models import TidyError
from .registry import Registry
from .updates import validate_download

EXTENSIONS = ('.tar.gz', '.tgz', '.tar.xz', '.zip', '.rpm', '.deb', '.appimage')
MAX_ITEMS = 10000


def identity(path):
    info = path.lstat()
    if info.st_uid != os.getuid() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise TidyError(f'Unsafe or unowned path: {path}')
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def canonical(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise TidyError('Symbolic links and noncanonical paths are not supported.')
    identity(path)
    return path


def digest(path, cancel=None):
    before = identity(path)
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if stream.fileno() < 0 or os.fstat(stream.fileno()).st_ino != before[1]:
            raise TidyError('File changed during inspection.')
        while chunk := stream.read(1024 * 1024):
            check_cancel(cancel)
            h.update(chunk)
    if identity(path) != before:
        raise TidyError('File changed during inspection.')
    return h.hexdigest()


def tree(path, cancel=None):
    root = identity(path)
    pending, entries, size = [(path, 0)], [], 0
    while pending:
        check_cancel(cancel)
        current, depth = pending.pop()
        info = identity(current)
        if info[0] != root[0] or depth > 12 or len(entries) >= MAX_ITEMS:
            raise TidyError('Folder exceeds inspection bounds or crosses a filesystem.')
        checksum = digest(current, cancel) if stat.S_ISREG(info[2]) else None
        entries.append((str(current.relative_to(path)), info, checksum))
        if checksum is None:
            for child in current.iterdir():
                check_cancel(cancel)
                if len(pending) + len(entries) >= MAX_ITEMS:
                    raise TidyError('Folder exceeds inspection bounds.')
                pending.append((child, depth + 1))
        else:
            size += info[3]
    return tuple(sorted(entries)), size


@dataclass(frozen=True)
class Item:
    path: Path
    scope: Path
    snapshot: tuple
    size: int
    category: str
    reason: str
    owned: bool = False
    checksum: str | None = None
    keeper: Path | None = None


class Cleanup:
    def __init__(self, paths):
        self.paths = paths

    def guard(self, path):
        canonical(path)
        root = self.paths.root.resolve()
        if path == root or path.is_relative_to(root) or root.is_relative_to(path):
            raise TidyError('Managed storage and its ancestors cannot be selected here.')
        for app in self.paths.apps.iterdir():
            record = Registry(self.paths).load(app)
            active = Path(record.install_dir)
            if path == active or path.is_relative_to(active) or active.is_relative_to(path):
                raise TidyError('An active installation overlaps this selection.')

    def scan(self, location, *, requested=False, cancel=None, progress=None):
        if not requested:
            raise TidyError('Scanning requires an explicit action.')
        with self.paths.locked():
            location = canonical(location)
            self.guard(location)
            if not location.is_dir():
                raise TidyError('Choose a directory.')
            files, warnings = [], []
            for count, path in enumerate(location.iterdir()):
                check_cancel(cancel)
                if count >= MAX_ITEMS:
                    warnings.append('Item limit reached; scan is incomplete.')
                    break
                if path.name.lower().endswith(EXTENSIONS):
                    try:
                        snap = identity(path)
                        if stat.S_ISREG(snap[2]):
                            files.append((path, snap))
                    except (OSError, TidyError) as exc:
                        warnings.append(str(exc))
            sizes = {}
            for path, snap in files:
                sizes.setdefault(snap[3], []).append(path)
            tracked = {}
            for directory in (self.paths.apps, self.paths.rollback):
                for app in directory.iterdir():
                    record = Registry(self.paths).load(app, app_id=app.name)
                    tracked[(record.source, record.source_size)] = record.checksum
            groups, items = {}, []
            for index, (path, snap) in enumerate(files):
                check_cancel(cancel)
                if progress:
                    progress(f'Inspecting {path.name}', index / max(len(files), 1))
                expected = tracked.get((str(path), snap[3]))
                if len(sizes[snap[3]]) < 2 and not expected:
                    continue
                checksum = digest(path, cancel)
                groups.setdefault((snap[3], checksum), []).append((path, snap, expected == checksum))
            for (size, checksum), group in groups.items():
                group.sort(key=lambda x: str(x[0]))
                keeper = group[0][0] if len(group) > 1 else None
                for path, snap, tracked_match in group:
                    if keeper and path != keeper:
                        items.append(Item(path, location, snap, size, 'Exact duplicate download',
                                          f'Full SHA-256 match. Retain: {keeper}', checksum=checksum, keeper=keeper))
                    elif tracked_match and not keeper:
                        items.append(Item(path, location, snap, size, 'Previously tracked installation archive',
                                          'Matches a manifest checksum; necessity is unverified.', checksum=checksum))
            if any(self.paths.transactions.iterdir()):
                warnings.append('Pending recovery: managed leftovers are unavailable.')
            else:
                for directory in self.paths.downloads.iterdir():
                    try:
                        validate_download(self.paths, directory)
                        snapshot, size = tree(directory, cancel)
                        items.append(Item(directory, self.paths.downloads, snapshot, size,
                                          'Known TidyLinux temporary leftover',
                                          'Validated download marker; no pending transaction.', True))
                    except (OSError, ValueError, TidyError) as exc:
                        warnings.append(f'Preserved {directory}: {exc}')
            if any(self.paths.staging.iterdir()):
                warnings.append('Staging files preserved: ownership/recovery classification is ambiguous.')
            return items, warnings

    def inspect_folder(self, path, *, requested=False, cancel=None):
        if not requested:
            raise TidyError('Folder inspection requires an explicit action.')
        with self.paths.locked():
            path = canonical(path)
            self.guard(path)
            if not path.is_dir():
                raise TidyError('Choose a folder.')
            snapshot, size = tree(path, cancel)
            return Item(path, path.parent, snapshot, size, 'Manually selected folder for review',
                        f'Unverified; {len(snapshot)} entries. May contain an external installation.')

    def trash(self, items, *, confirmed=False, folder_confirmed=False, cancel=None, mover=None):
        if not confirmed:
            raise TidyError('Moving to Trash requires confirmation.')
        from .installer import native_trash
        mover = mover or native_trash
        results = []
        with self.paths.locked():
            for item in items:
                check_cancel(cancel)
                try:
                    if any(self.paths.transactions.iterdir()):
                        raise TidyError('Pending recovery; cleanup is blocked.')
                    canonical(item.scope)
                    canonical(item.path)
                    if item.path.parent != item.scope:
                        raise TidyError('Item is outside the authorized scope.')
                    if item.owned:
                        validate_download(self.paths, item.path)
                    else:
                        self.guard(item.path)
                    folder = item.path.is_dir()
                    if folder and not item.owned and not folder_confirmed:
                        raise TidyError('Unverified folders need separate confirmation.')
                    snapshot = tree(item.path, cancel)[0] if folder else identity(item.path)
                    if snapshot != item.snapshot:
                        raise TidyError('Item changed since scan; skipped.')
                    if item.checksum and digest(item.path, cancel) != item.checksum:
                        raise TidyError('Contents changed; skipped.')
                    if item.keeper:
                        canonical(item.keeper)
                        if item.keeper.parent != item.scope or digest(item.keeper, cancel) != item.checksum:
                            raise TidyError('Retained duplicate is missing or changed; skipped.')
                    if (tree(item.path, cancel)[0] if folder else identity(item.path)) != item.snapshot:
                        raise TidyError('Item changed; skipped.')
                    mover(item.path)
                    results.append(f'Moved to Trash: {item.path}. Space is not necessarily reclaimed.')
                except (OSError, ValueError, TidyError) as exc:
                    results.append(f'Skipped {item.path}: {exc}')
        return results
