"""All filesystem fixtures live in a private temporary tree, never the real home."""
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest import TestCase, mock
import gzip
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
import uuid
import zipfile

from tidylinux.archive import Limits, extract_archive, inspect_archive, safe_relative
from tidylinux.desktop import desktop_content, desktop_exec, digest, exec_argument
from tidylinux.installer import Installer
from tidylinux.models import Cancelled, TidyError
from tidylinux.registry import Paths, Registry

SCRIPT = b'#!/bin/sh\nprintf ready > launched.txt\n'


class CoreTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tidylinux-test-")
        self.root = Path(self.temp.name)
        self.paths = Paths(self.root / 'data with spaces % $ " `')
        self.installer = Installer(self.paths)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, members=None, kind="gz"):
        path = self.root / ("download.zip" if kind == "zip" else "download.tar." + kind)
        members = members if members is not None else [("My App/run", SCRIPT, 0o755), ("My App/resource.txt", b"resource", 0o644)]
        if kind == "zip":
            with zipfile.ZipFile(path, "w") as archive:
                for name, data, mode in members:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    archive.writestr(info, data)
        else:
            with tarfile.open(path, "w:" + kind) as archive:
                for name, data, mode in members:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode
                    archive.addfile(info, io.BytesIO(data))
        return path

    def install(self):
        inspected = inspect_archive(self.archive())
        return self.installer.install(inspected, inspected.candidates[0].path, 'My App % " $ `', "1.2")

    def test_supported_formats_and_magic_not_extension(self):
        for kind in ("gz", "xz", "zip"):
            with self.subTest(kind=kind):
                path = self.archive(kind=kind)
                renamed = path.with_suffix(".unknown")
                path.rename(renamed)
                result = inspect_archive(renamed)
                self.assertEqual(result.top_directory, "My App")
                self.assertEqual(result.candidates[0].path, "My App/run")
                self.assertEqual(result.entries, 2)
                self.assertEqual(len(result.checksum), 64)

    def test_app_logo_preferred_over_extension_and_documentation_icons(self):
        logo = "VSCode-linux-x64/resources/app/resources/linux/code.png"
        path = self.archive([
            ("VSCode-linux-x64/chrome-sandbox", b"\x7fELF", 0o755),
            ("VSCode-linux-x64/code", b"\x7fELF", 0o755),
            ("VSCode-linux-x64/resources/app/extensions/markdown/icon.png", b"extension", 0o644),
            ("VSCode-linux-x64/docs/code.png", b"documentation", 0o644),
            ("VSCode-linux-x64/icon.png", b"generic", 0o644),
            (logo, b"logo", 0o644),
        ])
        inspection = inspect_archive(path)
        self.assertEqual(inspection.icons[0], logo)
        record = self.installer.install(inspection, "VSCode-linux-x64/code", "VS Code")
        self.assertEqual(record.icon, str(Path(record.install_dir) / logo))
        self.assertIn(f"Icon={record.icon}", Path(record.desktop_path).read_text())

    def appimage(self, image_type=2):
        path = self.root / "My App % test.AppImage"
        header = b"\x7fELF\x02\x01\x01\x00AI" + bytes([image_type]) + b"\0" * 53
        path.write_bytes(header + b"fixture payload; never executed")
        path.chmod(0o600)
        return path

    def test_appimage_install_launch_uninstall(self):
        for image_type in (1, 2):
            with self.subTest(image_type=image_type):
                path = self.appimage(image_type)
                inspection = inspect_archive(path)
                self.assertEqual(inspection.format, "appimage")
                self.assertEqual(inspection.suggested_name, "My App % test")
                self.assertEqual(inspection.entries, 1)
                self.assertEqual(inspection.expanded_size, path.stat().st_size)
                with mock.patch("tidylinux.installer.subprocess.Popen") as spawn:
                    record = self.installer.install(inspection, inspection.candidates[0].path, "My App")
                    spawn.assert_not_called()
                    executable = Path(record.install_dir) / record.executable
                    self.assertEqual(executable.read_bytes(), path.read_bytes())
                    self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertEqual(self.installer.registry.inventory(), ([record], []))
                    self.installer.launch(record)
                    self.assertEqual(spawn.call_args.args[0], [str(executable)])
                    self.assertIn(f"Exec={desktop_exec(str(executable))}", Path(record.desktop_path).read_text())
                self.installer.uninstall(record)
                self.assertFalse(Path(record.install_dir).exists())
                self.assertFalse(Path(record.desktop_path).exists())
                self.assertTrue(path.exists())

    def test_appimage_detection_requires_content_and_handles_renaming(self):
        path = self.appimage()
        renamed = path.with_suffix(".bin")
        path.rename(renamed)
        self.assertEqual(inspect_archive(renamed).format, "appimage")
        for data in (SCRIPT, b"\x7fELF" + b"\0" * 64, b"\x7fELF\x02\x01\x01\x00AI\x02"):
            path.write_bytes(data)
            with self.assertRaises(TidyError):
                inspect_archive(path)
        with self.assertRaisesRegex(TidyError, "unsupported AppImage"):
            inspect_archive(self.appimage(3))

    def test_appimage_embedded_logo_used_in_desktop_shortcut(self):
        if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
            self.skipTest("squashfs-tools not installed")
        appdir = self.root / "AppDir"
        appdir.mkdir()
        # A standard .DirIcon link must be resolved inside the image.
        logo = b"\x89PNG\r\n\x1a\n" + b"fixture"
        (appdir / "app.png").write_bytes(logo)
        (appdir / ".DirIcon").symlink_to("app.png")
        filesystem = self.root / "filesystem.squashfs"
        subprocess.run(["mksquashfs", str(appdir), str(filesystem), "-noappend", "-processors", "1", "-quiet"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        source = self.appimage()
        source.write_bytes(source.read_bytes() + filesystem.read_bytes())
        inspection = inspect_archive(source)
        record = self.installer.install(inspection, inspection.candidates[0].path, "App with logo")
        self.assertEqual(Path(record.icon).read_bytes(), logo)
        self.assertFalse(Path(record.icon).is_symlink())
        self.assertEqual(record.installed_size, source.stat().st_size + len(logo))
        self.assertIn(f"Icon={record.icon}\n", Path(record.desktop_path).read_text())
        self.installer.uninstall(record)
        self.assertFalse(Path(record.icon).exists())

    def test_appimage_icon_missing_tool_and_excessive_output(self):
        from tidylinux.appimage import read_icon
        source = self.appimage()
        with mock.patch("tidylinux.appimage.shutil.which", return_value=None):
            self.assertIsNone(read_icon(source))
        # Exercise the subprocess output bound without trusting image metadata.
        tool = self.root / "fake-unsquashfs"
        tool.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'\\x89PNG\\r\\n\\x1a\\n' + b'x' * (5 * 1024 * 1024))\n")
        tool.chmod(0o700)
        with mock.patch("tidylinux.appimage.shutil.which", return_value=str(tool)), \
             mock.patch("tidylinux.appimage.squashfs_offset", return_value=64):
            self.assertIsNone(read_icon(source))

    def test_appimage_limits_cancellation_and_source_change(self):
        path = self.appimage()
        for limits in (Limits(entries=0), Limits(file_bytes=1), Limits(expanded_bytes=1), Limits(archive_bytes=1)):
            with self.subTest(limits=limits), self.assertRaises(TidyError):
                inspect_archive(path, limits)
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            inspect_archive(path, cancel=cancel)
        destination = self.root / "appimage-copy"
        destination.mkdir()
        with self.assertRaises(Cancelled):
            extract_archive(path, destination, cancel=cancel)
        self.assertEqual(list(destination.iterdir()), [])
        inspection = inspect_archive(path)
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(TidyError, "changed since inspection"):
            self.installer.install(inspection, inspection.candidates[0].path, "My App")
        self.assertEqual(list(self.paths.apps.iterdir()), [])
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_corrupt_and_unsupported(self):
        path = self.root / "broken.tar.gz"
        for data in (b"not an archive", b"\x1f\x8bgarbage", b"\xfd7zXZ\x00garbage"):
            with self.subTest(data=data):
                path.write_bytes(data)
                with self.assertRaises(TidyError):
                    inspect_archive(path)

    def test_traversal_and_absolute(self):
        for name in ("../escape", "/tmp/escape", "app/../../escape", "a\\..\\escape", "a\nfile"):
            for kind in ("gz", "zip"):
                with self.subTest(name=name, kind=kind):
                    with self.assertRaises(TidyError):
                        inspect_archive(self.archive([(name, SCRIPT, 0o755)], kind))
        self.assertFalse((self.root / "escape").exists())

    def test_tar_links_and_special_files(self):
        for member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE):
            with self.subTest(member_type=member_type):
                path = self.root / "unsafe.tar.gz"
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo("unsafe")
                    member.type, member.linkname = member_type, "../../outside"
                    archive.addfile(member)
                with self.assertRaises(TidyError):
                    inspect_archive(path)

    def test_zip_symlinks(self):
        path = self.root / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../../outside")
        with self.assertRaises(TidyError):
            inspect_archive(path)

    def test_oversized_declaration(self):
        path = self.root / "bomb.tar.gz"
        member = tarfile.TarInfo("huge")
        member.size = 5 * 1024**3
        with gzip.open(path, "wb") as stream:
            stream.write(member.tobuf())
            stream.write(b"\0" * 1024)
        with self.assertRaisesRegex(TidyError, "size limit"):
            inspect_archive(path)

    def test_limits(self):
        path = self.archive()
        for limits in (Limits(entries=1), Limits(expanded_bytes=4), Limits(archive_bytes=1), Limits(file_bytes=1)):
            with self.subTest(limits=limits):
                with self.assertRaises(TidyError):
                    inspect_archive(path, limits)

    def test_compression_ratio(self):
        path = self.archive([("run", SCRIPT, 0o755), ("zero", b"\0" * 100000, 0o600)])
        with self.assertRaisesRegex(TidyError, "compression ratio"):
            inspect_archive(path, Limits(ratio=2))

    def test_multiple_candidates_and_icons(self):
        path = self.archive([("app/a", SCRIPT, 0o755), ("app/b", b"\x7fELF" + b"\0" * 30, 0o644),
                             ("app/icon.png", b"icon", 0o644), ("app/lib.so", b"\x7fELF", 0o755)])
        result = inspect_archive(path)
        self.assertEqual([c.path for c in result.candidates], ["app/a", "app/b"])
        self.assertEqual(result.icons, ("app/icon.png",))

    def test_installer_scripts_and_no_executable(self):
        for name, data in (("install.sh", SCRIPT), ("setup.sh", SCRIPT), ("readme.txt", b"hello")):
            with self.subTest(name=name), self.assertRaisesRegex(TidyError, "No portable"):
                inspect_archive(self.archive([(name, data, 0o755)]))

    def test_duplicates_and_parent_file(self):
        for members in ([('a', SCRIPT, 0o755), ('a', SCRIPT, 0o755)],
                        [('a', SCRIPT, 0o755), ('a/b', SCRIPT, 0o755)]):
            with self.assertRaises(TidyError):
                inspect_archive(self.archive(members))

    def test_private_extraction_and_permissions(self):
        path = self.archive([("./app/run", SCRIPT, 0o4755)])
        destination = self.root / "extract"
        destination.mkdir(mode=0o700)
        extract_archive(path, destination)
        self.assertEqual(stat.S_IMODE((destination / "app/run").stat().st_mode), 0o700)
        with self.assertRaises(TidyError):
            extract_archive(path, destination)

    def test_extract_revalidates_unsafe(self):
        path = self.archive([("../escape", SCRIPT, 0o755)])
        destination = self.root / "extract"
        destination.mkdir(mode=0o700)
        with self.assertRaises(TidyError):
            extract_archive(path, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_install_persist_desktop_source_preserved(self):
        record = self.install()
        self.assertTrue(Path(record.source).is_file())
        self.assertEqual(self.installer.registry.inventory(), ([record], []))
        self.assertEqual(Registry(self.paths).load(self.paths.apps / record.app_id), record)
        content = Path(record.desktop_path).read_bytes()
        self.assertEqual(digest(content), record.desktop_checksum)
        self.assertIn(b"Type=Application", content)
        self.assertIn(b"%%", content)
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", record.desktop_path], check=True)
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_launch_with_resources_spaces_and_missing(self):
        record = self.install()
        # Check working directory using an isolated, harmless fixture launcher.
        process = self.installer.launch(record)
        process.wait(timeout=5)
        self.assertEqual((Path(record.install_dir) / "My App/launched.txt").read_text(), "ready")
        (Path(record.install_dir) / record.executable).unlink()
        with self.assertRaisesRegex(TidyError, "missing"):
            self.installer.launch(record)

    def test_launch_error(self):
        record = self.install()
        with mock.patch("tidylinux.installer.subprocess.Popen", side_effect=OSError("Exec format error")):
            with self.assertRaisesRegex(TidyError, "Could not launch"):
                self.installer.launch(record)

    def test_uninstall_only_managed(self):
        record = self.install()
        external = self.root / "user-settings"
        external.write_text("keep")
        unknown = self.paths.apps / "not-managed"
        unknown.mkdir()
        # Even an injected symlink inside an owned tree must not delete its target.
        (Path(record.install_dir) / "outside").symlink_to(external)
        self.installer.uninstall(record)
        self.assertFalse((self.paths.apps / record.app_id).exists())
        self.assertFalse(Path(record.desktop_path).exists())
        self.assertEqual(external.read_text(), "keep")
        self.assertTrue(Path(record.source).exists())
        self.assertTrue(unknown.exists())
        with self.assertRaises(TidyError):
            self.installer.uninstall(replace(record, app_id="not-managed"))

    def test_desktop_external_modification_refused(self):
        record = self.install()
        Path(record.desktop_path).write_text("someone else's shortcut")
        with self.assertRaisesRegex(TidyError, "changed outside"):
            self.installer.uninstall(record)
        self.assertTrue(Path(record.install_dir).is_dir())

    def test_desktop_symlink_refused(self):
        record = self.install()
        target = self.root / "outside.desktop"
        target.write_text("keep")
        Path(record.desktop_path).unlink()
        Path(record.desktop_path).symlink_to(target)
        with self.assertRaises(TidyError):
            self.installer.uninstall(record)
        self.assertEqual(target.read_text(), "keep")

    def test_missing_desktop_can_uninstall(self):
        record = self.install()
        Path(record.desktop_path).unlink()
        self.installer.uninstall(record)

    def test_corrupt_manifests_graceful(self):
        record = self.install()
        path = self.paths.apps / record.app_id / "manifest.json"
        original = json.loads(path.read_text())
        for content in ("{bad", "[]", json.dumps({**original, "install_dir": str(self.root)}),
                        json.dumps({**original, "executable": "../../outside"}),
                        json.dumps({**original, "source_size": "big"}),
                        json.dumps({**original, "name": "x" * 201}),
                        json.dumps({**original, "version": "x" * 101}),
                        json.dumps({**original, "icon": record.install_dir + "/../outside"})):
            with self.subTest(content=content):
                path.write_text(content)
                records, warnings = self.installer.registry.inventory()
                self.assertEqual(records, [])
                self.assertTrue(warnings)
                with self.assertRaises(TidyError):
                    self.installer.uninstall(record)
                self.assertTrue(Path(record.install_dir).is_dir())

    def test_symlink_manifest_and_payload(self):
        record = self.install()
        directory = self.paths.apps / record.app_id
        manifest = directory / "manifest.json"
        outside = self.root / "record.json"
        manifest.rename(outside)
        manifest.symlink_to(outside)
        with self.assertRaises(TidyError):
            self.installer.registry.load(directory)
        manifest.unlink()
        outside.rename(manifest)
        shutil.rmtree(directory / "payload")
        (directory / "payload").symlink_to(self.root)
        with self.assertRaises(TidyError):
            self.installer.uninstall(record)

    def test_missing_payload_shown_with_warning(self):
        record = self.install()
        shutil.rmtree(record.install_dir)
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [record])
        self.assertTrue(warnings)

    def test_id_collision_no_overwrite(self):
        identity = uuid.UUID("12345678123456781234567812345678")
        with mock.patch("tidylinux.installer.uuid.uuid4", return_value=identity):
            record = self.install()
            with self.assertRaisesRegex(TidyError, "collision"):
                self.install()
        self.assertEqual(self.installer.registry.inventory()[0], [record])

    def test_cancel_cleans_staging(self):
        inspection = inspect_archive(self.archive())
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.installer.install(inspection, inspection.candidates[0].path, "App", cancel=cancel)
        self.assertEqual(list(self.paths.staging.iterdir()), [])
        self.assertEqual(list(self.paths.apps.iterdir()), [])

    def test_failure_after_publish_rolls_back(self):
        inspection = inspect_archive(self.archive())
        with mock.patch("tidylinux.installer.os.link", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.installer.install(inspection, inspection.candidates[0].path, "App")
        self.assertEqual(list(self.paths.staging.iterdir()), [])
        self.assertEqual(list(self.paths.apps.iterdir()), [])
        self.assertEqual(list(self.paths.desktops.iterdir()), [])
        self.assertTrue(inspection.source.exists())

    def test_failure_after_desktop_publish_rolls_back(self):
        inspection = inspect_archive(self.archive())
        with mock.patch("tidylinux.installer.sync_directory", side_effect=[None, OSError("disk failure")]):
            with self.assertRaises(OSError):
                self.installer.install(inspection, inspection.candidates[0].path, "App")
        self.assertEqual(list(self.paths.apps.iterdir()), [])
        self.assertEqual(list(self.paths.desktops.iterdir()), [])
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_changed_source_refused_even_same_metadata(self):
        path = self.archive()
        inspected = inspect_archive(path)
        self.archive([("My App/run", SCRIPT.replace(b"ready", b"other"), 0o755), ("My App/resource.txt", b"resource", 0o644)])
        with self.assertRaisesRegex(TidyError, "changed"):
            self.installer.install(inspected, inspected.candidates[0].path, "App")
        self.assertEqual(list(self.paths.apps.iterdir()), [])

    def test_invalid_selection_and_names(self):
        inspection = inspect_archive(self.archive())
        for name, executable in (("", "My App/run"), ("Bad\nName", "My App/run"), ("App", "missing")):
            with self.assertRaises(TidyError):
                self.installer.install(inspection, executable, name)

    def test_lock_contention_and_symlink_root(self):
        with self.paths.locked():
            with self.assertRaisesRegex(TidyError, "progress"):
                with self.paths.locked():
                    self.fail("second lock acquired")
            with self.assertRaisesRegex(TidyError, "progress"):
                self.installer.registry.inventory()
        isolated = Paths(self.root / "isolated")
        isolated.data.mkdir()
        isolated.root.symlink_to(self.paths.root)
        with self.assertRaises(TidyError):
            isolated.prepare()

    def test_normalization(self):
        self.assertEqual(safe_relative("./app/run"), "app/run")
        self.assertEqual(exec_argument("/a path/100%"), '"/a path/100%%"')

    def test_hidden_tar_metadata_is_bounded(self):
        path = self.root / "pax-bomb.tar.gz"
        member = tarfile.TarInfo("pax")
        member.type = tarfile.XHDTYPE
        member.size = 2 * 1024**3
        with gzip.open(path, "wb") as stream:
            stream.write(member.tobuf())
            stream.write(b"\0" * 1024)
        with self.assertRaisesRegex(TidyError, "metadata"):
            inspect_archive(path)

    def test_regular_pax_long_paths(self):
        path = self.archive([("app/" + "a" * 120 + "/run", SCRIPT, 0o755)])
        inspected = inspect_archive(path)
        record = self.installer.install(inspected, inspected.candidates[0].path, "Long paths")
        self.assertTrue((Path(record.install_dir) / record.executable).is_file())

    def test_root_directory_entries_count_toward_limit(self):
        path = self.root / "many.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for _ in range(10):
                member = tarfile.TarInfo(".")
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
        with self.assertRaisesRegex(TidyError, "too many"):
            inspect_archive(path, Limits(entries=4))

    def test_zip_directory_count_forgery(self):
        import struct
        path = self.archive(kind="zip")
        data = bytearray(path.read_bytes())
        index = data.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", data, index + 8, 1, 1)
        path.write_bytes(data)
        with self.assertRaisesRegex(TidyError, "count"):
            inspect_archive(path)

    def test_extraction_failure_cleanup(self):
        inspection = inspect_archive(self.archive())
        def fail_extract(path, destination, *args):
            (destination / "partial").write_text("partial data")
            raise TidyError("fixture extraction failed")
        with mock.patch("tidylinux.installer.extract_archive", side_effect=fail_extract):
            with self.assertRaisesRegex(TidyError, "extraction failed"):
                self.installer.install(inspection, inspection.candidates[0].path, "App")
        self.assertEqual(list(self.paths.staging.iterdir()), [])
        self.assertEqual(list(self.paths.apps.iterdir()), [])

    def test_uninstall_desktop_failure_restores_registration(self):
        record = self.install()
        original_unlink = Path.unlink
        def fail_desktop(path, *args, **kwargs):
            if path == Path(record.desktop_path):
                raise PermissionError("desktop permission denied")
            return original_unlink(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", fail_desktop):
            with self.assertRaises(PermissionError):
                self.installer.uninstall(record)
        self.assertEqual(self.installer.registry.inventory()[0], [record])
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_trash_source_checks_identity_and_preserves_on_failure(self):
        from tidylinux.installer import trash_source
        record = self.install()
        path = Path(record.source)
        with mock.patch("tidylinux.installer.native_trash") as trash:
            trash_source(record)
            trash.assert_called_once_with(path)
        with mock.patch("tidylinux.installer.native_trash", side_effect=OSError("Trash unavailable")):
            with self.assertRaises(OSError):
                trash_source(record)
        self.assertTrue(path.exists())
        path.write_bytes(b"changed")
        with mock.patch("tidylinux.installer.native_trash") as trash:
            with self.assertRaises(TidyError):
                trash_source(record)
            trash.assert_not_called()
        self.assertEqual(path.read_bytes(), b"changed")

    def test_trash_source_rejects_symlink(self):
        from tidylinux.installer import trash_source
        record = self.install()
        path = Path(record.source)
        target = self.root / "outside.tar.gz"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(TidyError):
            trash_source(record)
        self.assertTrue(target.is_file())

    def test_native_elf_fixture(self):
        true = shutil.which("true")
        if not true:
            self.skipTest("No native true executable available")
        inspected = inspect_archive(self.archive([("Native App/run", Path(true).read_bytes(), 0o755)]))
        record = self.installer.install(inspected, "Native App/run", "Native fixture")
        process = self.installer.launch(record)
        self.assertEqual(process.wait(timeout=5), 0)

    def test_helper_selection_is_rejected_before_writing(self):
        inspected = inspect_archive(self.archive([("bundle/code", SCRIPT, 0o755),
                                                  ("bundle/chrome-sandbox", SCRIPT, 0o755)]))
        with self.assertRaisesRegex(TidyError, "Helper program"):
            self.installer.install(inspected, "bundle/chrome-sandbox", "App")
        self.assertFalse(self.paths.root.exists())

    def test_realistic_portable_bundle_lifecycle(self):
        from tidylinux.launchers import recommend_launcher
        launcher = b'#!/bin/sh\ncat resources/message.txt > launched.txt\n'
        for kind in ("gz", "xz", "zip"):
            with self.subTest(kind=kind):
                source = self.archive([
                    ("Demo-linux-x64/demo", launcher, 0o755),
                    ("Demo-linux-x64/alternate.sh", launcher, 0o755),
                    ("Demo-linux-x64/chrome-sandbox", SCRIPT, 0o755),
                    ("Demo-linux-x64/resources/message.txt", b"fixture resource", 0o644),
                    ("Demo-linux-x64/lib/runtime.so.1", b"\x7fELF", 0o755),
                ], kind)
                inspected = inspect_archive(source)
                self.assertEqual(recommend_launcher(inspected)[0], "Demo-linux-x64/demo")
                self.assertNotIn("Demo-linux-x64/lib/runtime.so.1", [c.path for c in inspected.candidates])
                # Honor a valid explicit selection even when another is recommended.
                record = self.installer.install(inspected, "Demo-linux-x64/alternate.sh", "Demo")
                restarted = Installer(Paths(self.paths.data))
                self.assertEqual(restarted.registry.inventory(), ([record], []))
                environment = {**os.environ, "XDG_DATA_HOME": str(self.paths.data),
                               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
                subprocess.run([sys.executable, "-c",
                                "from tidylinux.registry import Paths, Registry; import sys; "
                                "records, warnings = Registry(Paths.default()).inventory(); "
                                "assert not warnings and [r.app_id for r in records] == [sys.argv[1]]",
                                record.app_id], env=environment, check=True, capture_output=True)
                self.assertIn(exec_argument(str(Path(record.install_dir) / record.executable)),
                              Path(record.desktop_path).read_text())
                self.assertEqual(restarted.launch(record).wait(timeout=5), 0)
                self.assertEqual((Path(record.install_dir) / "Demo-linux-x64/launched.txt").read_bytes(), b"fixture resource")
                unrelated = self.paths.desktops / "unrelated.desktop"
                unrelated.write_text("keep")
                restarted.uninstall(record)
                self.assertEqual(unrelated.read_text(), "keep")
                self.assertTrue(source.is_file())
                self.assertFalse(Path(record.desktop_path).exists())
                self.assertEqual(restarted.registry.inventory(), ([], []))
                self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_internal_links_still_rejected(self):
        path = self.root / "portable-links.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            entry = tarfile.TarInfo("Demo/bin/demo")
            entry.size, entry.mode = len(SCRIPT), 0o755
            archive.addfile(entry, io.BytesIO(SCRIPT))
            entry = tarfile.TarInfo("Demo/AppRun")
            entry.type, entry.linkname = tarfile.SYMTYPE, "bin/demo"
            archive.addfile(entry)
        with self.assertRaisesRegex(TidyError, "links"):
            inspect_archive(path)
        self.assertTrue(path.exists())

    def test_executable_name_containing_so_is_discovered(self):
        inspected = inspect_archive(self.archive([("app/demo.something", b"\x7fELF", 0o755)]))
        self.assertEqual(inspected.candidates[0].path, "app/demo.something")

    def test_nonregular_manifest_rejected_without_reading(self):
        record = self.install()
        manifest = self.paths.apps / record.app_id / "manifest.json"
        manifest.unlink()
        os.mkfifo(manifest)
        with self.assertRaisesRegex(TidyError, "invalid manifest file"):
            self.installer.registry.load(manifest.parent)

    def test_foreign_manifest_owner_rejected(self):
        record = self.install()
        with mock.patch("tidylinux.registry.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(TidyError, "invalid manifest file"):
                self.installer.registry.load(self.paths.apps / record.app_id)

    def test_staging_leftovers_are_reported_and_preserved(self):
        self.paths.prepare()
        leftover = self.paths.staging / "remove-interrupted"
        leftover.mkdir()
        (leftover / "user-data").write_text("keep")
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [])
        self.assertTrue(any("Interrupted-operation" in warning for warning in warnings))
        self.assertEqual((leftover / "user-data").read_text(), "keep")

    def test_failed_manifest_write_rolls_back(self):
        inspected = inspect_archive(self.archive())
        with mock.patch("tidylinux.installer.fsync_file", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.installer.install(inspected, inspected.candidates[0].path, "Demo")
        self.assertEqual(list(self.paths.apps.iterdir()), [])
        self.assertEqual(list(self.paths.desktops.iterdir()), [])
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_desktop_launch_handles_quoted_paths(self):
        try:
            import gi
            gi.require_version("GioUnix", "2.0")
            from gi.repository import GioUnix
        except (ImportError, ValueError):
            self.skipTest("GioUnix unavailable; desktop launch needs Fedora's PyGObject")
        for directory in ('plain', 'spaces % $ " `', 'backslash\\path'):
            with self.subTest(directory=directory):
                self.installer = Installer(Paths(self.root / directory))
                record = self.install()
                info = GioUnix.DesktopAppInfo.new_from_filename(record.desktop_path)
                self.assertIsNotNone(info)
                self.assertTrue(info.launch([], None))
                result = Path(record.install_dir) / "My App/launched.txt"
                deadline = time.monotonic() + 5
                while (not result.exists() or result.read_text() != "ready") and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(result.read_text(), "ready")
                self.installer.uninstall(record)

    def test_own_launcher_setup_remove_and_refuse_overwrite(self):
        import sys
        script = Path(__file__).resolve().parents[1] / "scripts/desktop_launcher.py"
        environment = {**os.environ, "XDG_DATA_HOME": str(self.paths.data)}
        command = [sys.executable, str(script)]
        subprocess.run(command + ["install"], env=environment, check=True, capture_output=True)
        launcher = self.paths.desktops / "io.github.tidylinux.TidyLinux.desktop"
        self.assertTrue(launcher.is_file())
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(launcher)], check=True)
        subprocess.run(command + ["install"], env=environment, check=True, capture_output=True)
        subprocess.run(command + ["uninstall"], env=environment, check=True, capture_output=True)
        self.assertFalse(launcher.exists())
        launcher.write_text("unrelated entry")
        for action in ("install", "uninstall"):
            result = subprocess.run(command + [action], env=environment, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(launcher.read_text(), "unrelated entry")
