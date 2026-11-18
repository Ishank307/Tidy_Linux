import tempfile
import unittest
from pathlib import Path
from threading import Event
from tidylinux.cleanup import Cleanup
from tidylinux.registry import Paths
from tidylinux.models import TidyError, Cancelled


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = Paths(root / 'data')
        self.paths.prepare()
        self.location = root / 'downloads'
        self.location.mkdir()
        self.cleanup = Cleanup(self.paths)

    def duplicates(self):
        for name in ('a.zip', 'b.zip', 'c.zip'):
            (self.location / name).write_bytes(b'identical')
        return self.cleanup.scan(self.location, requested=True)[0]

    def test_explicit_scan_and_trash(self):
        with self.assertRaises(TidyError):
            self.cleanup.scan(self.location)
        items = self.duplicates()
        with self.assertRaises(TidyError):
            self.cleanup.trash(items)

    def test_duplicates_retain_one(self):
        items = self.duplicates()
        self.assertEqual(len(items), 2)
        self.cleanup.trash(items, confirmed=True, mover=lambda p: p.unlink())
        self.assertEqual([p.name for p in self.location.iterdir()], ['a.zip'])

    def test_changed_keeper_preserves_duplicates(self):
        items = self.duplicates()
        (self.location / 'a.zip').write_bytes(b'changed')
        moved = []
        self.cleanup.trash(items, confirmed=True, mover=moved.append)
        self.assertEqual(moved, [])

    def test_changed_item_and_symlink(self):
        items = self.duplicates()
        items[0].path.unlink()
        items[0].path.symlink_to(self.location / 'a.zip')
        moved = []
        self.cleanup.trash([items[0]], confirmed=True, mover=moved.append)
        self.assertEqual(moved, [])

    def test_no_name_based_or_recursive_detection(self):
        (self.location / 'a.zip').write_bytes(b'one')
        (self.location / 'b.zip').write_bytes(b'two')
        nested = self.location / 'nested'
        nested.mkdir()
        (nested / 'a.zip').write_bytes(b'one')
        self.assertEqual(self.cleanup.scan(self.location, requested=True)[0], [])

    def test_cancel(self):
        self.duplicates()
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.cleanup.scan(self.location, requested=True, cancel=cancel)

    def test_managed_storage_blocked(self):
        with self.assertRaises(TidyError):
            self.cleanup.inspect_folder(self.paths.apps, requested=True)

    def test_unverified_folder_requires_separate_approval(self):
        item = self.cleanup.inspect_folder(self.location, requested=True)
        moved = []
        self.cleanup.trash([item], confirmed=True, mover=moved.append)
        self.assertEqual(moved, [])
        self.cleanup.trash([item], confirmed=True, folder_confirmed=True, mover=moved.append)
        self.assertEqual(moved, [self.location])

    def test_pending_recovery_and_trash_failure_preserve(self):
        items = self.duplicates()
        journal = self.paths.transactions / 'unknown'
        journal.mkdir()
        moved = []
        self.cleanup.trash(items, confirmed=True, mover=moved.append)
        self.assertEqual(moved, [])
        journal.rmdir()
        def fail(path):
            raise TidyError('Trash unavailable')
        results = self.cleanup.trash(items, confirmed=True, mover=fail)
        self.assertTrue(all('Skipped' in r for r in results))
        self.assertTrue(all(i.path.exists() for i in items))
