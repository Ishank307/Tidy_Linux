"""Journaled replacement of one owned bundle. All callers hold Paths.locked().

STAGING leaves the active installation untouched. PREPARED is undoable and
recovery restores the original. COMMITTED keeps the new active and one backup.
Every rename and journal publication is followed by a parent-directory fsync.
Unknown or damaged data stops recovery; it is never guessed at or deleted.
"""
from pathlib import Path
from threading import Event
from typing import Callable
import hashlib
import json
import os
import re
import shutil
import stat
import uuid

from .archive import Limits, check_cancel
from .desktop import desktop_content, digest, verify_desktop
from .models import AppRecord, TidyError
from .registry import ID_PATTERN, Paths, Registry


def sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536:
        raise TidyError(f"Invalid transaction metadata: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TidyError(f"Invalid transaction metadata: {path}")
    return value


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".pending")
    # An interrupted write belongs to the same verified journal/bundle only.
    if temporary.exists() or temporary.is_symlink():
        raise TidyError(f"Pending metadata left untouched: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def fingerprint(bundle: Path, cancel: Event | None = None, durable: bool = False) -> str:
    """Seal manifest, relative paths, modes and file contents; reject unsafe trees."""
    metadata = bundle.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077
            or not {p.name for p in bundle.iterdir()} <= {
                "manifest.json", "payload", "desktop.backup", "rollback.json"}):
        raise TidyError(f"Unrecognized managed bundle metadata left untouched: {bundle}")
    checksum = hashlib.sha256()
    total = count = 0
    items = [bundle / "manifest.json", bundle / "payload"]
    for item in items:
        pending = [item]
        while pending:
            check_cancel(cancel)
            path = pending.pop()
            info = path.lstat()
            count += 1
            if (info.st_uid != os.getuid() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
                    or count > Limits().entries + 30002):
                raise TidyError(f"Unsafe or oversized retained installation: {path}")
            checksum.update(json.dumps([str(path.relative_to(bundle)), stat.S_IMODE(info.st_mode)],
                                       ensure_ascii=True).encode() + b"\0")
            if stat.S_ISDIR(info.st_mode):
                pending.extend(sorted(path.iterdir(), reverse=True))
            else:
                checksum.update(str(info.st_size).encode() + b"\0")
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        check_cancel(cancel)
                        total += len(chunk)
                        if total > Limits().expanded_bytes + 65536:
                            raise TidyError("Retained installation exceeds the size limit.")
                        checksum.update(chunk)
                    if durable:
                        os.fsync(stream.fileno())
            checksum.update(b"\0")
    if durable:
        for directory, _, _ in os.walk(bundle / "payload", topdown=False):
            sync(Path(directory))
        sync(bundle)
    return checksum.hexdigest()


def validate_version(paths: Paths, bundle: Path, app_id: str,
                     expected: str | None = None) -> AppRecord:
    record = Registry(paths).load(bundle, app_id=app_id)
    receipt = read_json(bundle / "rollback.json")
    if (set(receipt) != {"schema", "app_id", "digest"} or type(receipt["schema"]) is not int or receipt["schema"] != 1
            or receipt["app_id"] != app_id or not isinstance(receipt["digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["digest"])
            or (expected is not None and receipt["digest"] != expected)
            or fingerprint(bundle) != receipt["digest"]):
        raise TidyError(f"Retained installation integrity check failed: {bundle}")
    launcher = bundle / "payload" / record.executable
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise TidyError(f"Retained launcher is missing or not executable: {bundle}")
    desktop = bundle / "desktop.backup"
    info = desktop.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536
            or digest(desktop.read_bytes()) != record.desktop_checksum):
        raise TidyError(f"Retained desktop entry is invalid: {bundle}")
    return record


def seal(bundle: Path, record: AppRecord, content: bytes, cancel: Event | None = None) -> str:
    if digest(content) != record.desktop_checksum:
        raise TidyError("Desktop entry does not match the managed record.")
    value = fingerprint(bundle, cancel, durable=True)
    launcher = bundle / "payload" / record.executable
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise TidyError("The managed launcher is missing or not executable; replacement was not activated.")
    atomic_write(bundle / "desktop.backup", content)
    atomic_write(bundle / "rollback.json", json.dumps(
        {"schema": 1, "app_id": record.app_id, "digest": value}).encode())
    return value


def move(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise TidyError(f"Destination already exists; left untouched: {destination}")
    source.rename(destination)
    sync(source.parent)
    if destination.parent != source.parent:
        sync(destination.parent)


class Replacement:
    def __init__(self, paths: Paths, transaction: Path):
        self.paths, self.directory = paths, transaction
        self.journal = read_json(transaction / "journal.json")
        j = self.journal
        if (set(j) != {"schema", "app_id", "state", "old", "new", "retained"}
                or type(j["schema"]) is not int or j["schema"] != 1 or not isinstance(j["app_id"], str)
                or not ID_PATTERN.fullmatch(j["app_id"])
                or j["state"] not in {"staging", "prepared", "committed"}
                or not isinstance(j["old"], str) or not re.fullmatch(r"[0-9a-f]{64}", j["old"])
                or any(v is not None and (not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v))
                       for v in (j["new"], j["retained"]))
                or (j["state"] != "staging" and j["new"] is None)):
            raise TidyError(f"Invalid recovery journal: {transaction}")
        info = transaction.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or transaction.parent != paths.transactions
                or not re.fullmatch(r"change-[0-9a-f]{32}", transaction.name)):
            raise TidyError(f"Unsafe recovery directory: {transaction}")
        if not {p.name for p in transaction.iterdir()} <= {
                "journal.json", "journal.json.pending", "work", "new", "old", "retained"}:
            raise TidyError(f"Unrecognized recovery files left untouched: {transaction}")
        self.active = paths.apps / j["app_id"]
        self.backup = paths.rollback / j["app_id"]
        self.desktop = paths.desktops / f'{j["app_id"]}.desktop'
        self.desktop_stage = paths.desktops / f".{transaction.name}.desktop"

    def state(self, state: str) -> None:
        value = {**self.journal, "state": state}
        atomic_write(self.directory / "journal.json", json.dumps(value).encode())
        self.journal = value

    def publish_desktop(self, bundle: Path) -> None:
        content = (bundle / "desktop.backup").read_bytes()
        if (self.desktop.is_file() and not self.desktop.is_symlink()
                and self.desktop.stat().st_size <= 65536 and self.desktop.read_bytes() == content):
            return
        if self.desktop_stage.exists() or self.desktop_stage.is_symlink():
            if (self.desktop_stage.is_symlink() or not self.desktop_stage.is_file()
                    or self.desktop_stage.stat().st_size > 65536
                    or digest(self.desktop_stage.read_bytes()) != digest(content)):
                raise TidyError("Unrecognized staged shortcut left untouched.")
        else:
            with self.desktop_stage.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            sync(self.paths.desktops)
        os.replace(self.desktop_stage, self.desktop)
        sync(self.paths.desktops)

    def recover(self) -> None:
        j, tx = self.journal, self.directory
        locations = [self.active, self.backup, tx / "old", tx / "new", tx / "retained"]
        found: dict[Path, str] = {}
        records: dict[Path, AppRecord] = {}
        for path in locations:
            if path.exists() or path.is_symlink():
                record = validate_version(self.paths, path, j["app_id"])
                value = read_json(path / "rollback.json")["digest"]
                if (value not in {j["old"], j["new"], j["retained"]}
                        and not (j["state"] == "staging" and path == tx / "new")):
                    raise TidyError(f"Unrecognized installation left untouched: {path}")
                found[path], records[path] = value, record
        if self.desktop.is_symlink() or (self.desktop.exists() and
                (not self.desktop.is_file() or self.desktop.stat().st_size > 65536 or
                 digest(self.desktop.read_bytes()) not in {r.desktop_checksum for r in records.values()})):
            raise TidyError("Desktop entry changed outside TidyLinux; recovery requires manual review.")
        if self.desktop_stage.exists() or self.desktop_stage.is_symlink():
            info = self.desktop_stage.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536
                    or not any((path / "desktop.backup").read_bytes().startswith(self.desktop_stage.read_bytes())
                               for path in records)):
                raise TidyError("Unrecognized staged shortcut left untouched.")
            self.desktop_stage.unlink()
            sync(self.paths.desktops)
        if j["state"] == "committed":
            if found.get(self.active) != j["new"] or found.get(self.backup) != j["old"]:
                raise TidyError("Committed installation or backup is missing; versions were preserved.")
            self.publish_desktop(self.active)
        else:
            original = next((p for p in (self.active, tx / "old", self.backup)
                             if found.get(p) == j["old"]), None)
            if original is None:
                raise TidyError("Original installation is missing; recovery requires manual review.")
            if original != self.active:
                if self.active in found:
                    move(self.active, tx / "new")
                move(original, self.active)
            self.publish_desktop(self.active)
            if (tx / "retained") in found:
                move(tx / "retained", self.backup)
            if j["retained"] is not None:
                validate_version(self.paths, self.backup, j["app_id"], j["retained"])
        # Only a validated, namespaced journal authorizes deletion of its staging
        # tree. rmtree never follows symlinks; neither active nor backup is inside.
        if self.desktop_stage.exists():
            raise TidyError("Staged shortcut remains; inspect recovery files before cleanup.")
        shutil.rmtree(tx)
        sync(self.paths.transactions)


def recover_transactions(paths: Paths) -> list[str]:
    warnings = []
    for directory in sorted(paths.transactions.iterdir()):
        try:
            transaction = Replacement(paths, directory)
            transaction.recover()
            warnings.append(f"Recovered interrupted replacement for {transaction.journal['app_id']}.")
        except (OSError, ValueError, TypeError, TidyError) as exc:
            warnings.append(f"Recovery needs attention; all retained versions were preserved at {directory}: {exc}")
    return warnings


def require_recovered(paths: Paths) -> None:
    warnings = recover_transactions(paths)
    if any(paths.transactions.iterdir()):
        raise TidyError("\n".join(warnings))


def replace_installation(paths: Paths, current: AppRecord,
                         prepare: Callable[[Path], AppRecord],
                         cancel: Event | None = None) -> AppRecord:
    """Prepare an owned candidate, then durably activate it or undo on failure."""
    active, backup = paths.apps / current.app_id, paths.rollback / current.app_id
    # Persist the namespace entries created by Paths.prepare before recording
    # any operation that can move the only active installation into them.
    sync(paths.root)
    sync(paths.data)
    verify_desktop(current)
    old_content = (Path(current.desktop_path).read_bytes() if Path(current.desktop_path).exists()
                   else desktop_content(current).encode())
    old = seal(active, current, old_content, cancel)
    retained = None
    if backup.exists() or backup.is_symlink():
        validate_version(paths, backup, current.app_id)
        retained = read_json(backup / "rollback.json")["digest"]
    tx = paths.transactions / ("change-" + uuid.uuid4().hex)
    desktop_stage = paths.desktops / f".{tx.name}.desktop"
    if desktop_stage.exists() or desktop_stage.is_symlink():
        raise TidyError(f"An unrelated staged shortcut was left untouched: {desktop_stage}")
    tx.mkdir(mode=0o700)
    journal = {"schema": 1, "app_id": current.app_id, "state": "staging",
               "old": old, "new": None, "retained": retained}
    atomic_write(tx / "journal.json", json.dumps(journal).encode())
    sync(paths.transactions)
    transaction = Replacement(paths, tx)
    try:
        candidate = prepare(tx)
        transaction.journal["new"] = read_json(tx / "new" / "rollback.json")["digest"]
        validate_version(paths, tx / "new", current.app_id, transaction.journal["new"])
        check_cancel(cancel)
        verify_desktop(current)
        transaction.state("prepared")
        if retained is not None:
            move(backup, tx / "retained")
        move(active, tx / "old")
        move(tx / "new", active)
        verify_desktop(current)
        transaction.publish_desktop(active)
        move(tx / "old", backup)
        transaction.state("committed")
    except Exception as exc:
        try:
            # Read the durable state, not an uncommitted in-memory transition.
            recovery_transaction = Replacement(paths, tx)
            recovery_transaction.recover()
        except Exception as recovery:
            raise TidyError(f"Replacement failed: {exc}. Versions preserved at {tx}; recovery needs attention: {recovery}") from exc
        if recovery_transaction.journal["state"] == "committed":
            return candidate
        raise
    # A cleanup failure must not report an already committed update as failed.
    # Inventory will retry recovery and expose any retained journal.
    try:
        transaction.recover()
    except (OSError, ValueError, TidyError):
        pass
    return candidate
