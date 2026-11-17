"""Strict manifests and an XDG-scoped inventory. Unknown files are never removed."""
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
import fcntl
import json
import os
import re
import stat

from .archive import safe_relative
from .models import AppRecord, TidyError

ID_PATTERN = re.compile(r"tidylinux-[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class Paths:
    data: Path

    @classmethod
    def default(cls):
        base = os.environ.get("XDG_DATA_HOME", "")
        return cls(Path(base) if base and Path(base).is_absolute() else Path.home() / ".local/share")

    @property
    def root(self):
        return self.data / "tidylinux"

    @property
    def apps(self):
        return self.root / "apps"

    @property
    def staging(self):
        return self.root / "staging"

    @property
    def desktops(self):
        return self.data / "applications"

    @property
    def rollback(self):
        return self.root / "rollback"

    @property
    def transactions(self):
        return self.root / "transactions"

    def prepare(self):
        if not self.data.is_absolute():
            raise TidyError("XDG data directory must be absolute.")
        for path in (self.root, self.apps, self.staging, self.desktops, self.rollback, self.transactions):
            if path.is_symlink():
                raise TidyError(f"Managed directory is a symbolic link: {path}")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not path.is_dir() or path.stat().st_uid != os.getuid():
                raise TidyError(f"Managed directory is not owned by this user: {path}")
        for path in (self.root, self.apps, self.staging, self.rollback, self.transactions):
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise TidyError(f"Managed directory must be private (mode 0700): {path}")

    @contextmanager
    def locked(self):
        self.prepare()
        descriptor = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TidyError("Another TidyLinux operation is in progress. Try again shortly.") from exc
            yield
        finally:
            os.close(descriptor)


class Registry:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.rollback_ids: set[str] = set()
        self.rollback_versions: dict[str, AppRecord] = {}

    def load(self, directory: Path, *, app_id: str | None = None) -> AppRecord:
        try:
            identity = app_id or directory.name
            canonical = self.paths.apps / identity
            stored = (directory == self.paths.rollback / identity or
                      (directory.parent.parent == self.paths.transactions and
                       re.fullmatch(r"change-[0-9a-f]{32}", directory.parent.name) and
                       directory.name in {"new", "old", "retained"}))
            if (not ID_PATTERN.fullmatch(identity) or directory.is_symlink()
                    or (directory != canonical and not (app_id and stored))):
                raise ValueError("invalid managed directory")
            manifest = directory / "manifest.json"
            metadata = manifest.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or metadata.st_size > 65536 or directory.stat().st_uid != os.getuid()):
                raise ValueError("invalid manifest file")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {f.name for f in fields(AppRecord)}:
                raise ValueError("invalid manifest fields")
            record = AppRecord(**raw)
            strings = ("app_id", "name", "version", "install_dir", "executable", "source", "checksum", "installed_at", "desktop_path", "desktop_checksum")
            if any(not isinstance(getattr(record, key), str) for key in strings):
                raise ValueError("invalid manifest value types")
            if type(record.schema) is not int or record.schema != 1 or record.app_id != identity:
                raise ValueError("unsupported manifest version or ID")
            if (not record.name.strip() or len(record.name) > 200 or len(record.version) > 100
                    or any(ord(c) < 32 for c in record.name + record.version)):
                raise ValueError("invalid application name or version")
            if any(type(v) is not int or v < 0 for v in (record.source_size, record.installed_size)):
                raise ValueError("invalid size")
            if not re.fullmatch(r"[a-f0-9]{64}", record.checksum) or not re.fullmatch(r"[a-f0-9]{64}", record.desktop_checksum):
                raise ValueError("invalid checksum")
            payload = canonical / "payload"
            if record.install_dir != str(payload) or (directory / "payload").is_symlink():
                raise ValueError("invalid installation path")
            relative = safe_relative(record.executable)
            if relative != record.executable:
                raise ValueError("noncanonical executable path")
            if record.desktop_path != str(self.paths.desktops / f"{record.app_id}.desktop"):
                raise ValueError("invalid desktop path")
            if record.icon is not None:
                if not isinstance(record.icon, str) or not Path(record.icon).is_relative_to(payload):
                    raise ValueError("invalid icon path")
                icon_relative = str(Path(record.icon).relative_to(payload))
                if safe_relative(icon_relative) != icon_relative:
                    raise ValueError("invalid icon path")
            return record
        except (OSError, ValueError, TypeError, KeyError, TidyError) as exc:
            raise TidyError(f"Cannot load {directory.name}: {exc}") from exc

    def inventory(self) -> tuple[list[AppRecord], list[str]]:
        with self.paths.locked():
            return self._inventory()

    def _inventory(self) -> tuple[list[AppRecord], list[str]]:
        from .transactions import recover_transactions
        records, warnings = [], recover_transactions(self.paths)
        self.rollback_ids = set()
        self.rollback_versions = {}
        # Inspect under the mutation lock so an active operation is not reported
        # as crash debris. Never delete interrupted-operation data automatically.
        if any(self.paths.staging.iterdir()):
            warnings.append(f"Interrupted-operation files remain in {self.paths.staging}. "
                            "Close all TidyLinux instances before inspecting them; no files were removed.")
        for directory in sorted(self.paths.apps.iterdir()):
            if not ID_PATTERN.fullmatch(directory.name):
                warnings.append(f"Unrecognized item left untouched: {directory.name}")
                continue
            try:
                record = self.load(directory)
                records.append(record)
                if any((directory / name).exists() or (directory / name).is_symlink()
                       for name in ("desktop.backup.pending", "rollback.json.pending")):
                    warnings.append(f"{record.name}: interrupted backup metadata remains in {directory}; "
                                    "close all instances before inspecting it. The active installation was preserved.")
                if not Path(record.install_dir).is_dir():
                    warnings.append(f"{record.name}: installation directory is missing.")
                backup = self.paths.rollback / record.app_id
                if backup.exists() or backup.is_symlink():
                    from .transactions import validate_version
                    try:
                        previous = validate_version(self.paths, backup, record.app_id)
                        self.rollback_ids.add(record.app_id)
                        self.rollback_versions[record.app_id] = previous
                    except (OSError, ValueError, TidyError) as exc:
                        warnings.append(f"{record.name}: rollback is unavailable; retained files were preserved: {exc}")
            except TidyError as exc:
                warnings.append(str(exc))
        return sorted(records, key=lambda r: r.name.casefold()), warnings
