"""Replacement fault injection uses disposable XDG roots, never real apps."""
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest import TestCase, mock
import errno
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

from tidylinux.archive import inspect_archive
from tidylinux.desktop import digest
from tidylinux.installer import Installer
from tidylinux.models import Cancelled, TidyError
from tidylinux.registry import Paths
from tidylinux import transactions as tx


class PowerLoss(BaseException):
    """Skip normal exception cleanup, like a killed process."""


class UpdateTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tidylinux-updates-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = Paths(self.root / 'data % " $ `')
        self.installer = Installer(self.paths)
        self.old_source = self.bundle("1")
        inspected = inspect_archive(self.old_source)
        self.original = self.installer.install(inspected, "Demo-1/run", "Demo", "1")
        self.new_source = self.bundle("2")
        self.inspection = inspect_archive(self.new_source)
        self.external = self.root / "settings"
        self.external.write_text("keep settings")

    def bundle(self, version, kind="gz"):
        path = self.root / f"download-{version}.{kind}"
        members = [(f"Demo-{version}/run", f"#!/bin/sh\nprintf '{version}' > result\n".encode()),
                   (f"Demo-{version}/icon.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>')]
        if kind == "zip":
            with zipfile.ZipFile(path, "w") as archive:
                for name, content in members:
                    entry = zipfile.ZipInfo(name)
                    entry.external_attr = (stat.S_IFREG | (0o755 if name.endswith("run") else 0o644)) << 16
                    archive.writestr(entry, content)
        else:
            with tarfile.open(path, "w:" + kind) as archive:
                for name, content in members:
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode = len(content), 0o755 if name.endswith("run") else 0o644
                    archive.addfile(entry, io.BytesIO(content))
        return path

    def update(self, record=None, version="2"):
        return self.installer.update(record or self.original, self.inspection, "Demo-2/run", version)

    def assert_original(self):
        self.assertEqual(self.installer.registry.load(self.paths.apps / self.original.app_id), self.original)
        self.assertTrue((Path(self.original.install_dir) / self.original.executable).is_file())
        self.assertEqual(digest(Path(self.original.desktop_path).read_bytes()), self.original.desktop_checksum)
        self.assertEqual(self.external.read_text(), "keep settings")
        self.assertTrue(self.old_source.exists())
        self.assertTrue(self.new_source.exists())

    def test_success_identity_shortcut_source_and_external_data(self):
        updated = self.update()
        self.assertEqual(updated.app_id, self.original.app_id)
        self.assertEqual(updated.desktop_path, self.original.desktop_path)
        self.assertEqual(updated.install_dir, self.original.install_dir)
        self.assertEqual(updated.name, self.original.name)
        self.assertEqual(updated.version, "2")
        self.assertEqual(self.installer.registry.inventory(), ([updated], []))
        self.assertEqual(list(self.paths.apps.iterdir()), [self.paths.apps / updated.app_id])
        self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / updated.app_id, updated.app_id), self.original)
        self.assertEqual(self.installer.launch(updated).wait(timeout=5), 0)
        self.assertEqual((Path(updated.install_dir) / "Demo-2/result").read_text(), "2")
        self.assertTrue(self.old_source.exists() and self.new_source.exists())
        self.assertEqual(self.external.read_text(), "keep settings")
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", updated.desktop_path], check=True)

    def test_unknown_version_is_an_explicit_replacement(self):
        updated = self.update(version="")
        self.assertEqual(updated.version, "")
        self.assertEqual(updated.app_id, self.original.app_id)

    def test_update_supported_archives(self):
        current = self.original
        for kind in ("gz", "xz", "zip"):
            with self.subTest(kind=kind):
                source = self.bundle(kind, kind)
                inspected = inspect_archive(source)
                current = self.installer.update(current, inspected, f"Demo-{kind}/run", kind)
                self.assertEqual(current.app_id, self.original.app_id)
                self.assertTrue(source.exists())

    def test_appimage_types(self):
        current = self.original
        for image_type in (1, 2):
            with self.subTest(image_type=image_type):
                source = self.root / f"replacement-{image_type}.AppImage"
                source.write_bytes(b"\x7fELF\x02\x01\x01\0AI" + bytes([image_type]) + b"\0" * 53)
                inspected = inspect_archive(source)
                current = self.installer.update(current, inspected, "application.AppImage")
                self.assertEqual((Path(current.install_dir) / current.executable).read_bytes(), source.read_bytes())
                self.assertEqual(current.app_id, self.original.app_id)

    def test_rollback_restores_metadata_payload_icon_and_shortcut(self):
        updated = self.update()
        rolled_back = self.installer.rollback(updated)
        self.assertEqual(rolled_back, self.original)
        self.assert_original()
        self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / updated.app_id, updated.app_id), updated)
        self.assertEqual(self.installer.rollback(rolled_back), updated)

    def test_one_previous_version_after_repeated_updates(self):
        second = self.update()
        source = self.bundle("3")
        third = self.installer.update(second, inspect_archive(source), "Demo-3/run", "3")
        self.assertEqual(self.installer.rollback(third), second)
        self.assertEqual(list(self.paths.rollback.iterdir()), [self.paths.rollback / third.app_id])
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_changed_archive_and_invalid_launcher_preserve_active(self):
        self.new_source.write_bytes(self.new_source.read_bytes() + b"changed")
        with self.assertRaisesRegex(TidyError, "changed since inspection"):
            self.update()
        with self.assertRaisesRegex(TidyError, "Select an executable"):
            self.installer.update(self.original, self.inspection, "outside")
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_cancel_during_preparation(self):
        cancel = Event()
        original_stage = self.installer._stage
        def staged(*args):
            result = original_stage(*args)
            cancel.set()
            return result
        with mock.patch.object(self.installer, "_stage", side_effect=staged):
            with self.assertRaises(Cancelled):
                self.installer.update(self.original, self.inspection, "Demo-2/run", cancel=cancel)
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_disk_full_during_stage(self):
        with mock.patch("tidylinux.installer.fsync_file", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(OSError):
                self.update()
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_failed_shortcut_generation(self):
        with mock.patch("tidylinux.installer.desktop_content", side_effect=OSError("shortcut generation failed")):
            with self.assertRaises(OSError):
                self.update()
        self.assert_original()

    def test_failed_shortcut_publication_restores_previous(self):
        original_replace = os.replace
        def fail(source, destination):
            if Path(destination) == Path(self.original.desktop_path):
                raise OSError("shortcut publication failed")
            return original_replace(source, destination)
        with mock.patch("tidylinux.transactions.os.replace", side_effect=fail):
            with self.assertRaises(OSError):
                self.update()
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_partial_shortcut_disk_full(self):
        original_publish = tx.Replacement.publish_desktop
        calls = 0
        def fail(transaction, bundle):
            nonlocal calls
            calls += 1
            if calls == 1:
                transaction.desktop_stage.write_bytes((bundle / "desktop.backup").read_bytes()[:20])
                raise OSError(errno.ENOSPC, "disk full")
            return original_publish(transaction, bundle)
        with mock.patch.object(tx.Replacement, "publish_desktop", fail):
            with self.assertRaises(OSError):
                self.update()
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_interrupted_renames_recover_original(self):
        original_move = tx.move
        for interrupted in range(1, 4):
            with self.subTest(move=interrupted):
                calls = 0
                def crash(source, destination):
                    nonlocal calls
                    original_move(source, destination)
                    calls += 1
                    if calls == interrupted:
                        raise PowerLoss()
                with mock.patch("tidylinux.transactions.move", side_effect=crash):
                    with self.assertRaises(PowerLoss):
                        self.update()
                records, warnings = Installer(self.paths).registry.inventory()
                self.assertEqual(records, [self.original])
                self.assertTrue(any("Recovered interrupted" in warning for warning in warnings))
                self.assert_original()
                self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_interrupted_state_boundaries(self):
        original_state = tx.Replacement.state
        for state in ("prepared", "committed"):
            with self.subTest(state=state):
                def crash(transaction, value):
                    original_state(transaction, value)
                    if value == state:
                        raise PowerLoss()
                with mock.patch.object(tx.Replacement, "state", crash):
                    with self.assertRaises(PowerLoss):
                        self.update()
                records, warnings = self.installer.registry.inventory()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].version, "1" if state == "prepared" else "2")
                self.assertTrue(warnings)
                self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_interrupted_transaction_with_existing_backup(self):
        current = self.update()
        original_move = tx.move
        for interrupted in range(1, 5):
            with self.subTest(move=interrupted):
                calls = 0
                def crash(source, destination):
                    nonlocal calls
                    original_move(source, destination)
                    calls += 1
                    if calls == interrupted:
                        raise PowerLoss()
                source = self.bundle("3")
                with mock.patch("tidylinux.transactions.move", side_effect=crash):
                    with self.assertRaises(PowerLoss):
                        self.installer.update(current, inspect_archive(source), "Demo-3/run", "3")
                self.assertEqual(self.installer.registry.inventory()[0], [current])
                self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / current.app_id, current.app_id), self.original)
                self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_corrupt_rollback_metadata_and_payload_are_preserved(self):
        updated = self.update()
        backup = self.paths.rollback / updated.app_id
        manifest = backup / "manifest.json"
        original = manifest.read_bytes()
        corrupt = json.loads(original)
        corrupt["version"] = "unexpected"
        manifest.write_text(json.dumps(corrupt))
        with self.assertRaises(TidyError):
            self.installer.rollback(updated)
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [updated])
        self.assertTrue(warnings)
        self.assertNotIn(updated.app_id, self.installer.registry.rollback_ids)
        with self.assertRaises(TidyError):
            self.update(updated)
        with self.assertRaises(TidyError):
            self.installer.uninstall(updated)
        manifest.write_bytes(original)
        (backup / "payload" / self.original.executable).write_text("corrupt")
        with self.assertRaises(TidyError):
            self.installer.rollback(updated)
        self.assertTrue(backup.exists())
        self.assertEqual(self.installer.registry.load(self.paths.apps / updated.app_id), updated)

    def test_concurrent_mutations_and_stale_records_rejected(self):
        with self.paths.locked():
            for operation in (lambda: self.update(), lambda: self.installer.rollback(self.original),
                              lambda: self.installer.uninstall(self.original)):
                with self.subTest(operation=operation), self.assertRaisesRegex(TidyError, "progress"):
                    operation()
        updated = self.update()
        with self.assertRaisesRegex(TidyError, "record changed"):
            self.update()
        with self.assertRaises(TidyError):
            self.installer.update(replace(updated, app_id="external"), self.inspection, "Demo-2/run")

    def test_uninstall_removes_retained_version_preserves_external_files(self):
        updated = self.update()
        unrelated = self.paths.rollback / "unrelated"
        unrelated.mkdir()
        (unrelated / "file").write_text("keep")
        self.installer.uninstall(updated)
        self.assertFalse((self.paths.apps / updated.app_id).exists())
        self.assertFalse((self.paths.rollback / updated.app_id).exists())
        self.assertFalse(Path(updated.desktop_path).exists())
        self.assertEqual((unrelated / "file").read_text(), "keep")
        self.assertEqual(self.external.read_text(), "keep settings")
        self.assertTrue(self.old_source.exists() and self.new_source.exists())

    def test_unrecognized_recovery_journal_is_never_deleted(self):
        unknown = self.paths.transactions / "unrelated"
        unknown.mkdir()
        (unknown / "journal.json").write_text("{}")
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [self.original])
        self.assertTrue(warnings)
        self.assertTrue(unknown.exists())
        with self.assertRaises(TidyError):
            self.update()

    def test_real_process_exit_recovery(self):
        code = '''
import os, sys
from pathlib import Path
from tidylinux.archive import inspect_archive
from tidylinux.installer import Installer
from tidylinux.registry import Paths
from tidylinux import transactions as tx
installer = Installer(Paths(Path(sys.argv[1])))
record = installer.registry.inventory()[0][0]
original = tx.move
def crash(source, destination):
    original(source, destination)
    if source == installer.paths.apps / record.app_id:
        os._exit(91)
tx.move = crash
installer.update(record, inspect_archive(Path(sys.argv[2])), "Demo-2/run", "2")
'''
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        result = subprocess.run([sys.executable, "-c", code, str(self.paths.data), str(self.new_source)], env=environment)
        self.assertEqual(result.returncode, 91)
        records, warnings = Installer(self.paths).registry.inventory()
        self.assertEqual(records, [self.original])
        self.assertTrue(warnings)
        self.assert_original()

    def test_prepared_candidate_before_journal_transition(self):
        from tidylinux.installer import move as installer_move
        def crash(source, destination):
            installer_move(source, destination)
            if destination.name == "new":
                raise PowerLoss()
        with mock.patch("tidylinux.installer.move", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.update()
        self.assertEqual(self.installer.registry.inventory()[0], [self.original])
        self.assert_original()
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_interrupted_rollback_preserves_both_versions(self):
        updated = self.update()
        original_move = tx.move
        for interrupted in range(1, 5):
            with self.subTest(move=interrupted):
                calls = 0
                def crash(source, destination):
                    nonlocal calls
                    original_move(source, destination)
                    calls += 1
                    if calls == interrupted:
                        raise PowerLoss()
                with mock.patch("tidylinux.transactions.move", side_effect=crash):
                    with self.assertRaises(PowerLoss):
                        self.installer.rollback(updated)
                self.assertEqual(self.installer.registry.inventory()[0], [updated])
                self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / updated.app_id, updated.app_id), self.original)

    def test_disk_full_during_rollback_copy(self):
        updated = self.update()
        def partial(source, destination):
            destination.mkdir()
            (destination / "partial").write_text("incomplete")
            raise OSError(errno.ENOSPC, "disk full")
        with mock.patch("tidylinux.installer.shutil.copytree", side_effect=partial):
            with self.assertRaises(OSError):
                self.installer.rollback(updated)
        self.assertEqual(self.installer.registry.inventory(), ([updated], []))
        self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / updated.app_id, updated.app_id), self.original)
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_unsafe_active_tree_is_not_backed_up(self):
        link = Path(self.original.install_dir) / "external-link"
        for hard in (False, True):
            with self.subTest(hard=hard):
                if hard:
                    os.link(self.external, link)
                else:
                    link.symlink_to(self.external)
                with self.assertRaisesRegex(TidyError, "Unsafe"):
                    self.update()
                self.assertEqual(self.external.read_text(), "keep settings")
                link.unlink()
        self.assert_original()

    def test_foreign_desktop_prevents_update_and_uninstall(self):
        desktop = Path(self.original.desktop_path)
        desktop.write_text("unrelated shortcut")
        with self.assertRaises(TidyError):
            self.update()
        self.assertEqual(desktop.read_text(), "unrelated shortcut")
        self.assertEqual(self.installer.registry.load(self.paths.apps / self.original.app_id), self.original)

    def test_corrupt_recovery_data_never_discards_working_versions(self):
        original_move = tx.move
        def crash(source, destination):
            original_move(source, destination)
            if source == self.paths.apps / self.original.app_id:
                raise PowerLoss()
        with mock.patch("tidylinux.transactions.move", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.update()
        transaction = next(self.paths.transactions.iterdir())
        (transaction / "new" / "manifest.json").write_text("corrupt")
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [])
        self.assertTrue(any("Recovery needs attention" in warning for warning in warnings))
        self.assertTrue((transaction / "old" / "payload" / self.original.executable).is_file())
        self.assertTrue((transaction / "new").exists())
        with self.assertRaises(TidyError):
            self.installer.uninstall(self.original)

    def test_desktop_launch_after_update_and_rollback(self):
        try:
            import gi
            gi.require_version("GioUnix", "2.0")
            from gi.repository import GioUnix
        except (ImportError, ValueError):
            self.skipTest("GioUnix unavailable")
        current = self.update()
        for expected in ("2", "1"):
            info = GioUnix.DesktopAppInfo.new_from_filename(current.desktop_path)
            self.assertTrue(info.launch([], None))
            result = Path(current.install_dir) / Path(current.executable).parent / "result"
            deadline = time.monotonic() + 5
            while (not result.exists() or result.read_text() != expected) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(result.read_text(), expected)
            if expected == "2":
                current = self.installer.rollback(current)

    def test_interrupt_at_every_fsync_preserves_a_usable_installation(self):
        original_fsync = os.fsync
        calls = 0
        def count(descriptor):
            nonlocal calls
            calls += 1
            original_fsync(descriptor)
        with mock.patch("tidylinux.transactions.os.fsync", side_effect=count):
            self.update()
        for interrupted in range(1, calls + 1):
            with self.subTest(fsync=interrupted):
                installer = Installer(Paths(self.root / f"fsync-{interrupted}"))
                record = installer.install(inspect_archive(self.old_source), "Demo-1/run", "Demo", "1")
                counter = 0
                def crash(descriptor):
                    nonlocal counter
                    original_fsync(descriptor)
                    counter += 1
                    if counter == interrupted:
                        raise PowerLoss()
                with mock.patch("tidylinux.transactions.os.fsync", side_effect=crash):
                    with self.assertRaises(PowerLoss):
                        installer.update(record, self.inspection, "Demo-2/run", "2")
                records, warnings = installer.registry.inventory()
                self.assertEqual(len(records), 1, warnings)
                self.assertIn(records[0].version, ("1", "2"))
                self.assertTrue((Path(records[0].install_dir) / records[0].executable).is_file())
                self.assertTrue(any(path.is_file() for path in (
                    installer.paths.apps / record.app_id / "payload" / record.executable,
                    installer.paths.rollback / record.app_id / "payload" / record.executable)))

    def test_interrupted_recovery_can_be_retried(self):
        current = self.update()
        original_move = tx.move
        for interrupted in range(1, 4):
            with self.subTest(recovery_move=interrupted):
                source = self.bundle("3")
                def interrupt_update(source, destination):
                    original_move(source, destination)
                    if destination == self.paths.rollback / current.app_id:
                        raise PowerLoss()
                with mock.patch("tidylinux.transactions.move", side_effect=interrupt_update):
                    with self.assertRaises(PowerLoss):
                        self.installer.update(current, inspect_archive(source), "Demo-3/run", "3")
                count = 0
                def interrupt_recovery(source, destination):
                    nonlocal count
                    original_move(source, destination)
                    count += 1
                    if count == interrupted:
                        raise PowerLoss()
                with mock.patch("tidylinux.transactions.move", side_effect=interrupt_recovery):
                    with self.assertRaises(PowerLoss):
                        self.installer.registry.inventory()
                self.assertEqual(self.installer.registry.inventory()[0], [current])
                self.assertEqual(tx.validate_version(self.paths, self.paths.rollback / current.app_id, current.app_id), self.original)

    def test_cleanup_failure_keeps_committed_update_and_retries(self):
        original_remove = shutil.rmtree
        failed = False
        def fail(path, *args, **kwargs):
            nonlocal failed
            if Path(path).parent == self.paths.transactions and not failed:
                failed = True
                raise OSError("cleanup failed")
            return original_remove(path, *args, **kwargs)
        with mock.patch("tidylinux.transactions.shutil.rmtree", side_effect=fail):
            updated = self.update()
        self.assertTrue(any(self.paths.transactions.iterdir()))
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [updated])
        self.assertTrue(warnings)
        self.assertEqual(list(self.paths.transactions.iterdir()), [])

    def test_foreign_desktop_during_recovery_is_preserved(self):
        original_state = tx.Replacement.state
        def crash(transaction, state):
            original_state(transaction, state)
            if state == "prepared":
                raise PowerLoss()
        with mock.patch.object(tx.Replacement, "state", crash):
            with self.assertRaises(PowerLoss):
                self.update()
        desktop = Path(self.original.desktop_path)
        desktop.write_text("unrelated desktop entry")
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [self.original])
        self.assertTrue(any("manual review" in warning for warning in warnings))
        self.assertEqual(desktop.read_text(), "unrelated desktop entry")
        self.assertTrue(any(self.paths.transactions.iterdir()))
        self.assertTrue((Path(self.original.install_dir) / self.original.executable).is_file())

    def test_foreign_desktop_edit_during_activation_is_preserved(self):
        original_move = tx.move
        desktop = Path(self.original.desktop_path)
        def edit(source, destination):
            original_move(source, destination)
            if destination == self.paths.apps / self.original.app_id:
                desktop.write_text("externally edited during activation")
        with mock.patch("tidylinux.transactions.move", side_effect=edit):
            with self.assertRaisesRegex(TidyError, "recovery needs attention"):
                self.update()
        self.assertEqual(desktop.read_text(), "externally edited during activation")
        transaction = next(self.paths.transactions.iterdir())
        self.assertTrue((transaction / "old" / "payload" / self.original.executable).is_file())

    def test_preexisting_staged_shortcut_is_not_adopted_or_removed(self):
        import uuid
        identity = uuid.UUID("12345678123456781234567812345678")
        unexpected = self.paths.desktops / f".change-{identity.hex}.desktop"
        unexpected.write_bytes(b"")
        with mock.patch("tidylinux.transactions.uuid.uuid4", return_value=identity):
            with self.assertRaisesRegex(TidyError, "unrelated staged shortcut"):
                self.update()
        self.assertEqual(unexpected.read_bytes(), b"")
        self.assert_original()
