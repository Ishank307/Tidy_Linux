#!/usr/bin/env python3
"""Native GTK smoke check. Only temporary XDG directories and fixture apps are used."""
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    with tempfile.TemporaryDirectory(prefix="tidylinux-gui-") as temporary:
        root = Path(temporary)
        for key, name in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
            os.environ[key] = str(root / name)
        os.environ["GSETTINGS_BACKEND"] = "memory"
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib, Gtk
        if not Gtk.init_check():
            print("SKIP: no accessible graphical display", file=sys.stderr)
            return 77
        Adw.init()
        from tidylinux.app import TidyApplication
        from tidylinux.archive import inspect_archive
        from tidylinux.window import MainWindow
        errors = []
        def on_error(*exc):
            errors.append(exc)
            sys.__excepthook__(*exc)
        sys.excepthook = on_error
        application = TidyApplication()
        # Keep the test local even when the real desktop application is running.
        application.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        application.register(None)
        window = MainWindow(application)
        window.present()

        def pump_until(predicate, seconds=5):
            deadline = time.monotonic() + seconds
            context = GLib.MainContext.default()
            while not predicate():
                if time.monotonic() > deadline:
                    raise AssertionError("GUI operation timed out")
                while context.pending():
                    context.iteration(False)
                time.sleep(0.005)
            for _ in range(20):
                while context.pending():
                    context.iteration(False)
                time.sleep(0.005)
            if errors:
                raise AssertionError(errors)

        pump_until(lambda: not window.busy)
        assert window.stack.get_visible_child_name() == "empty"
        fixture = root / "sample.tar.gz"
        with tarfile.open(fixture, "w:gz") as archive:
            data = b"#!/bin/sh\nprintf launched > smoke-result\n"
            entry = tarfile.TarInfo("Sample App/run")
            entry.mode, entry.size = 0o755, len(data)
            archive.addfile(entry, io.BytesIO(data))
            icon = (Path(__file__).resolve().parents[1] / "data/icons/io.github.tidylinux.TidyLinux.svg").read_bytes()
            entry = tarfile.TarInfo("Sample App/icon.svg")
            entry.size = len(icon)
            archive.addfile(entry, io.BytesIO(icon))
        assert window.on_drop(None, Gio.File.new_for_path(str(fixture)), 0, 0)
        pump_until(lambda: not window.busy)
        assert window.get_visible_dialog().get_title() == "Review installation"
        window.get_visible_dialog().force_close()
        inspection = inspect_archive(fixture)
        # Multiple executable candidates require an explicit launcher choice.
        from dataclasses import replace
        from tidylinux.models import Candidate
        window.review(replace(inspection, candidates=inspection.candidates +
                              (Candidate("Sample App/helper", "Linux executable"),)))
        review = window.get_visible_dialog()

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from descendants(child)
                child = child.get_next_sibling()

        widgets = list(descendants(review))
        selector = next(w for w in widgets if isinstance(w, Gtk.DropDown))
        install_button = next(w for w in widgets if isinstance(w, Gtk.Button) and w.get_label() == "Install")
        assert selector.get_selected() == 0
        assert not install_button.get_sensitive()
        selector.set_selected(1)
        assert install_button.get_sensitive()
        name = next(w for w in widgets if isinstance(w, Adw.EntryRow) and w.get_title() == "Name")
        name.set_text("")
        assert not install_button.get_sensitive()
        name.set_text("Sample App")
        assert install_button.get_sensitive()
        review.force_close()
        window.run_job(lambda: window.installer.install(inspection, "Sample App/run", "Sample App"), window.installed, "Installing fixture…")
        pump_until(lambda: not window.busy)
        # Success callback queues an inventory refresh.
        pump_until(lambda: window.get_visible_dialog() is not None and window.get_visible_dialog().get_title() == "Application installed")
        assert window.stack.get_visible_child_name() == "apps"
        record = window.installer.registry.inventory()[0][0]
        window.get_visible_dialog().force_close()
        window.details(record)
        pump_until(lambda: window.get_visible_dialog() is not None)
        window.get_visible_dialog().force_close()
        window.launch(record)
        pump_until(lambda: not window.busy)
        assert (Path(record.install_dir) / "Sample App/smoke-result").read_text() == "launched"
        window.confirm_uninstall(record)
        dialog = window.get_visible_dialog()
        dialog.emit("response", "uninstall")
        dialog.force_close()
        pump_until(lambda: not window.busy)
        pump_until(lambda: window.stack.get_visible_child_name() == "empty")
        assert not Path(record.desktop_path).exists()
        assert fixture.exists()
        window.confirm_trash(record)
        dialog = window.get_visible_dialog()
        dialog.emit("response", "trash")
        dialog.force_close()
        pump_until(lambda: not window.busy)
        if fixture.exists():
            # Some system/internal mounts (including /tmp here) do not support Trash.
            dialog = window.get_visible_dialog()
            assert isinstance(dialog, Adw.AlertDialog)
            assert "archive was preserved" in dialog.get_body()
            dialog.force_close()
            trash_result = "native Trash failure feedback and source preservation"
        else:
            assert (root / "data/Trash/files/sample.tar.gz").is_file()
            trash_result = "native Trash success"
        # Exercise AppImage drop, review and installation without executing it.
        appimage = root / "Sample.AppImage"
        appimage.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\0" * 53 + b"fixture")
        assert window.on_drop(None, Gio.File.new_for_path(str(appimage)), 0, 0)
        pump_until(lambda: not window.busy)
        review = window.get_visible_dialog()
        assert review.get_title() == "Review installation"
        install_button = next(w for w in descendants(review) if isinstance(w, Gtk.Button) and w.get_label() == "Install")
        assert install_button.get_sensitive()
        install_button.emit("clicked")
        pump_until(lambda: not window.busy)
        pump_until(lambda: window.get_visible_dialog() is not None and window.get_visible_dialog().get_title() == "Application installed")
        record = window.installer.registry.inventory()[0][0]
        assert (Path(record.install_dir) / record.executable).read_bytes() == appimage.read_bytes()
        window.get_visible_dialog().force_close()
        window.run_job(lambda: window.installer.uninstall(record), lambda _: window.refresh(), "Removing AppImage…")
        pump_until(lambda: not window.busy)
        assert window.stack.get_visible_child_name() == "empty"
        assert appimage.exists()
        window.about()
        pump_until(lambda: window.get_visible_dialog() is not None)
        window.get_visible_dialog().force_close()
        # File dialog construction is checked without opening a native chooser.
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(Gtk.FileFilter(name="All files", patterns=["*"]))
        Gtk.FileDialog(title="Choose archive", filters=filters)
        window.close()
        print("PASS: native window, empty/list views, review/drop handler, icon, install, inventory refresh, details, launch, uninstall, AppImage install/remove, About, file dialog APIs; " + trash_result)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
