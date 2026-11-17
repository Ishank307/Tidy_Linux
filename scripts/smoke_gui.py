#!/usr/bin/env python3
"""Native GTK smoke check. Only temporary XDG directories and fixture apps are used."""
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
from threading import Event, get_ident

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
        from gi.repository import Adw, Gdk, Gio, GLib, Gtk
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            print("SKIP: no accessible graphical display", file=sys.stderr)
            return 77
        Adw.init()
        # Programmatic dialog replacement should not sample transition frames.
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        print(f"Environment: GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}, "
              f"Libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}")
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
        # A deliberately blocked worker must leave the GTK main context live.
        main_thread = get_ident()
        release = Event()
        ticks = []
        completed = []
        def slow_work():
            assert get_ident() != main_thread
            assert release.wait(3)
        def tick():
            ticks.append(get_ident())
            if len(ticks) == 5:
                release.set()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        window.run_job(slow_work, lambda _: completed.append(get_ident()), "Testing responsiveness…", False)
        assert window.on_close()  # Noncancellable work must not claim cancellation.
        assert not window.cancel.is_set()
        GLib.timeout_add(20, tick)
        pump_until(lambda: bool(completed))
        assert ticks == [main_thread] * 5 and completed == [main_thread]
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

        def capture(name):
            if "--screenshots" not in sys.argv:
                return
            gi.require_version("Graphene", "1.0")
            from gi.repository import Graphene
            destination = Path(sys.argv[sys.argv.index("--screenshots") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            snapshot = Gtk.Snapshot()
            Gtk.WidgetPaintable.new(window).snapshot(snapshot, window.get_width(), window.get_height())
            node = snapshot.to_node()
            assert node is not None
            texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, window.get_width(), window.get_height()))
            assert texture.save_to_png(str(destination / name))

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
        # Exercise recommendation and helper rejection through the real widgets.
        window.review(replace(inspection, candidates=inspection.candidates +
                              (Candidate("Sample App/chrome-sandbox", "Linux executable"),)))
        review = window.get_visible_dialog()
        widgets = list(descendants(review))
        selector = next(w for w in widgets if isinstance(w, Gtk.DropDown))
        install_button = next(w for w in widgets if isinstance(w, Gtk.Button) and w.get_label() == "Install")
        assert selector.get_selected() == 1 and install_button.get_sensitive()
        selector.set_selected(2)
        assert not install_button.get_sensitive()
        review.force_close()

        # Both color schemes and a compact window must allocate the review.
        style = Adw.StyleManager.get_default()
        window.set_default_size(480, 360)
        long_path = "Sample App/" + "long-directory/" * 12 + "launcher"
        window.review(replace(inspection, source=root / ("long-name-" * 20 + ".tar.gz"),
                              candidates=(Candidate(long_path, "Linux executable"),)))
        review = window.get_visible_dialog()
        pump_until(lambda: review.get_width() > 0)
        assert window.get_width() == 480
        selector = next(w for w in descendants(review) if isinstance(w, Gtk.DropDown))
        assert selector.get_width() <= review.get_width() <= window.get_width()
        capture("review-long-path.png")
        review.force_close()
        window.review(inspection)
        review = window.get_visible_dialog()
        for scheme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            style.set_color_scheme(scheme)
            pump_until(lambda: review.get_width() > 0)
            assert style.get_dark() == (scheme == Adw.ColorScheme.FORCE_DARK)
            assert review.get_width() <= window.get_width()
            button = next(w for w in descendants(review) if isinstance(w, Gtk.Button) and w.get_label() == "Install")
            success, bounds = button.compute_bounds(review)
            assert success and bounds.get_y() >= 0 and bounds.get_y() + bounds.get_height() <= review.get_height()
            capture("review-dark.png" if style.get_dark() else "review-light.png")
        style.set_color_scheme(Adw.ColorScheme.DEFAULT)
        install_button = next(w for w in descendants(review) if isinstance(w, Gtk.Button) and w.get_label() == "Install")
        install_button.emit("clicked")
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
        result_path = Path(record.install_dir) / "Sample App/smoke-result"
        pump_until(result_path.exists)
        assert result_path.read_text() == "launched"
        # Replace through the real review controls, retaining identity and the
        # existing shortcut. The initial fixture has no version information.
        replacement = root / "replacement.tar.xz"
        with tarfile.open(replacement, "w:xz") as archive:
            data = b"#!/bin/sh\nprintf replacement > smoke-result\n"
            entry = tarfile.TarInfo("Replacement/run")
            entry.mode, entry.size = 0o755, len(data)
            archive.addfile(entry, io.BytesIO(data))
        window.inspect(replacement, record)
        pump_until(lambda: not window.busy)
        review = window.get_visible_dialog()
        assert review.get_title() == "Replace installation"
        widgets = list(descendants(review))
        replace_button = next(w for w in widgets if isinstance(w, Gtk.Button) and w.get_label() == "Replace installation")
        assert replace_button.get_sensitive()
        version = next(w for w in widgets if isinstance(w, Adw.EntryRow) and w.get_title() == "New version (optional)")
        version.set_text("2")
        # An unknown current version must still use replacement wording.
        assert replace_button.get_label() == "Replace installation"
        assert any(isinstance(w, Gtk.Label) and "Version ordering cannot be verified" in w.get_text() for w in widgets)
        pump_until(lambda: review.get_mapped() and review.get_width() > 0)
        capture("update-review.png")
        replace_button.emit("clicked")
        pump_until(lambda: window.get_visible_dialog() is not None and window.get_visible_dialog().get_title() == "Application updated")
        updated = window.installer.registry.inventory()[0][0]
        assert updated.app_id == record.app_id and updated.desktop_path == record.desktop_path
        assert updated.version == "2"
        row_widgets = list(descendants(window.rows))
        assert any(isinstance(w, Gtk.Button) and w.get_label() == "Roll Back…" for w in row_widgets)
        window.get_visible_dialog().force_close()
        window.launch(updated)
        pump_until(lambda: not window.busy)
        new_result = Path(updated.install_dir) / "Replacement/smoke-result"
        pump_until(new_result.exists)
        assert new_result.read_text() == "replacement"
        window.confirm_rollback(updated)
        dialog = window.get_visible_dialog()
        dialog.emit("response", "rollback")
        dialog.force_close()
        pump_until(lambda: window.get_visible_dialog() is not None and window.get_visible_dialog().get_title() == "Application restored")
        assert window.installer.registry.inventory()[0] == [record]
        assert replacement.exists()
        window.get_visible_dialog().force_close()
        # With both labels present the review says Update, without asserting
        # that the replacement label sorts after the current label.
        window.review(inspect_archive(replacement), updated)
        review = window.get_visible_dialog()
        version = next(w for w in descendants(review) if isinstance(w, Adw.EntryRow) and w.get_title() == "New version (optional)")
        version.set_text("1")
        assert review.get_title() == "Review update"
        assert any(isinstance(w, Gtk.Button) and w.get_label() == "Update" for w in descendants(review))
        review.force_close()
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
        print("PASS: native window, empty/list views, review/drop handler, launcher guidance/helper rejection, light/dark compact review, responsive worker/main-thread completion, icon, install, inventory refresh, details, launch, update/replacement review, retained identity, rollback/menu, uninstall with backup, AppImage install/remove, About, file dialog APIs; " + trash_result)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
