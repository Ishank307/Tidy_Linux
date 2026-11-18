"""Small native interface. Blocking filesystem work stays on one worker thread."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
import logging

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .archive import inspect_archive
from . import __version__
from .installer import Installer, trash_source
from .models import Cancelled
from .launchers import helper_reason, recommend_launcher
from .updates import install_download
from .update_ui import OnlineUpdates

LOG = logging.getLogger(__name__)


def margins(widget, amount=18):
    for side in ("top", "bottom", "start", "end"):
        getattr(widget, f"set_margin_{side}")(amount)
    return widget


def label(text, css=None):
    result = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0)
    if css:
        result.add_css_class(css)
    return result


def size_text(size):
    return GLib.format_size(size)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="TidyLinux", default_width=760, default_height=540)
        self.installer = Installer()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tidylinux")
        self.busy = False
        self.cancel = Event()
        self.closing = False
        self.records = []
        self.inventory_warnings = []
        self.update_results = {}
        self.online = OnlineUpdates(self)
        self.connect("close-request", self.on_close)
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(layout)
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="TidyLinux", subtitle="Install clean. Stay organized.")
        header.set_title_widget(title)
        self.install_button = Gtk.Button(label="Install App", tooltip_text="Choose an archive or AppImage (Ctrl+O)")
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", self.choose_archive)
        header.pack_start(self.install_button)
        menu = Gio.Menu()
        menu.append("Cleanup…", "win.cleanup")
        menu.append("About TidyLinux", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        from .cleanup_ui import present_cleanup
        for name, callback in (("check-updates", self.online.check), ("cleanup", lambda *_: present_cleanup(self)), ("install", self.choose_archive), ("about", self.about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        layout.append(header)
        checks = Gtk.Box(halign=Gtk.Align.END)
        checks.set_margin_end(12)
        checks.set_margin_bottom(6)
        self.check_button = Gtk.Button(label="Check for Updates", tooltip_text="Manually check linked GitHub release sources")
        self.check_button.connect("clicked", self.online.check)
        checks.append(self.check_button)
        layout.append(checks)
        self.banner = Adw.Banner(title="", button_label="Details", revealed=False)
        self.banner.connect("button-clicked", lambda *_: self.message("Inventory needs attention", self.inventory_warning))
        layout.append(self.banner)
        self.stack = Gtk.Stack(vexpand=True)
        layout.append(self.stack)
        empty = Adw.StatusPage(icon_name="package-x-generic-symbolic", title="No apps installed yet",
                              description="Choose or drop an archive or AppImage to get started.")
        choose = Gtk.Button(label="Choose App", halign=Gtk.Align.CENTER)
        choose.add_css_class("suggested-action")
        choose.connect("clicked", self.choose_archive)
        empty.set_child(choose)
        self.stack.add_named(empty, "empty")
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.rows.add_css_class("boxed-list")
        clamp = Adw.Clamp(maximum_size=720)
        clamp.set_child(margins(self.rows))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        self.stack.add_named(scroller, "apps")
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.run_job(lambda: self.installer.registry.inventory(), self.show_inventory, "Loading applications…", cancellable=False)

    def on_close(self, *_):
        if self.busy:
            if self.cancellable:
                self.cancel.set()
                self.toast("Cancelling the current operation. Close again when it finishes.")
            else:
                self.toast("Wait for the current operation to finish, then close again.")
            return True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.closing = True
        return False

    def toast(self, text):
        self.overlay.add_toast(Adw.Toast(title=text, timeout=5))

    def message(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=str(body))
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def run_job(self, work, done, title, cancellable=True, *, progress=False, on_failure=None):
        if self.busy:
            self.toast("Wait for the current operation to finish.")
            return
        self.busy = True
        self.cancellable = cancellable
        self.cancel = Event()
        self.install_button.set_sensitive(False)
        self.check_button.set_sensitive(False)
        dialog = Adw.Dialog(title=title, content_width=340, can_close=False)
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18), 24)
        box.append(label(title, "title-3"))
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        box.append(spinner)
        if progress:
            status = label(title)
            bar = Gtk.ProgressBar()
            box.append(status)
            box.append(bar)
            token, lock = self.cancel, Lock()
            updates = {"pending": False, "latest": (title, 0)}
            def apply_progress():
                with lock:
                    text, fraction = updates["latest"]
                    updates["pending"] = False
                if self.busy and self.cancel is token:
                    status.set_text(text)
                    bar.set_fraction(max(0, min(1, fraction)))
                return GLib.SOURCE_REMOVE
            def report_progress(text, fraction):
                with lock:
                    updates["latest"] = text, fraction
                    if not updates["pending"]:
                        updates["pending"] = True
                        GLib.idle_add(apply_progress)
            self.report_progress = report_progress
        if cancellable:
            button = Gtk.Button(label="Cancel")
            button.connect("clicked", lambda *_: (self.cancel.set(), button.set_sensitive(False)))
            box.append(button)
        dialog.set_child(box)
        dialog.present(self)
        future = self.pool.submit(work)

        def complete():
            dialog.force_close()
            self.busy = False
            self.install_button.set_sensitive(True)
            self.check_button.set_sensitive(bool(self.records))
            try:
                result = future.result()
            except Cancelled as exc:
                if on_failure:
                    on_failure(exc)
                self.toast("Operation cancelled")
            except Exception as exc:
                if on_failure:
                    on_failure(exc)
                LOG.exception("Operation failed")
                self.message("Could not complete operation", str(exc))
            else:
                try:
                    done(result)
                except Exception as exc:
                    LOG.exception("Could not update interface")
                    self.message("Could not update interface", str(exc))
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(complete))

    def show_inventory(self, result):
        records, warnings = result
        self.records, self.inventory_warnings = records, warnings
        self.check_button.set_sensitive(bool(records) and not self.busy)
        while child := self.rows.get_first_child():
            self.rows.remove(child)
        for record in records:
            result = self.update_results.get(record.app_id)
            if result and (result.record != record or (result.release and
                    result.release.repository != self.installer.registry.update_sources.get(record.app_id))):
                self.update_results.pop(record.app_id, None)
                result = None
            status = result.status if result else ("Version unknown" if record.app_id in self.installer.registry.update_sources else "Not configured")
            row = Adw.ActionRow(title=GLib.markup_escape_text(record.name),
                                subtitle=GLib.markup_escape_text(" · ".join(filter(None, (status, record.version, size_text(record.installed_size))))))
            row.set_tooltip_text(result.detail if result else "No release check has been made. Use Check for Updates or configure Update Source.")
            row.set_title_lines(2)
            row.set_subtitle_lines(1)
            image = Gtk.Image(icon_name="application-x-executable-symbolic", pixel_size=32)
            if record.icon and Path(record.icon).is_file():
                image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(record.icon)))
            row.add_prefix(image)
            if result and result.status in {"Update available", "Version unknown"} and result.release and result.release.assets:
                review_release = Gtk.Button(label="Review release…", valign=Gtk.Align.CENTER)
                review_release.connect("clicked", lambda _, r=result: self.online.review_release(r))
                row.add_suffix(review_release)
            launch = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=f"Launch {record.name}")
            launch.add_css_class("flat")
            launch.connect("clicked", lambda _, r=record: self.launch(r))
            row.add_suffix(launch)
            popover = Gtk.Popover()
            actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            menu_actions = [("Update…", self.choose_update), ("Update Source…", self.online.configure)]
            if record.app_id in self.installer.registry.rollback_ids:
                menu_actions.append(("Roll Back…", self.confirm_rollback))
            menu_actions.extend((("Launch", self.launch), ("Details", self.details),
                                 ("Uninstall…", self.confirm_uninstall)))
            for text, callback in menu_actions:
                button = Gtk.Button(label=text)
                button.add_css_class("flat")
                button.connect("clicked", lambda _, cb=callback, r=record, p=popover: (p.popdown(), cb(r)))
                actions.append(button)
            popover.set_child(actions)
            row.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", popover=popover,
                                          valign=Gtk.Align.CENTER, tooltip_text=f"Actions for {record.name}"))
            self.rows.append(row)
        self.stack.set_visible_child_name("apps" if records else "empty")
        self.inventory_warning = "\n\n".join(warnings)
        self.banner.set_title("Some installation records need attention")
        self.banner.set_revealed(bool(warnings))

    def refresh(self):
        self.run_job(lambda: self.installer.registry.inventory(), self.show_inventory, "Refreshing applications…", False)

    def choose_update(self, record):
        self.choose_archive(update_record=record)

    def choose_archive(self, *_, update_record=None):
        if self.busy:
            return
        chooser = Gtk.FileDialog(title=(f"Choose replacement for {update_record.name}" if update_record
                                        else "Choose an application archive or AppImage"))
        filter_ = Gtk.FileFilter(name="App archives and AppImages")
        for pattern in ("*.tar.gz", "*.tgz", "*.tar.xz", "*.zip", "*.[Aa][Pp][Pp][Ii][Mm][Aa][Gg][Ee]"):
            filter_.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_)
        filters.append(Gtk.FileFilter(name="All files", patterns=["*"]))
        chooser.set_filters(filters)
        def selected(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file.get_path():
                    self.inspect(Path(file.get_path()), update_record)
                else:
                    self.message("Local file required", "Copy the application file to this computer first.")
            except GLib.Error as exc:
                if not exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self.message("Could not select application", exc.message)
        chooser.open(self, None, selected)

    def on_drop(self, _, file, x, y):
        if self.busy or not file.get_path():
            return False
        self.inspect(Path(file.get_path()))
        return True

    def inspect(self, path, update_record=None):
        self.run_job(lambda: inspect_archive(path, cancel=self.cancel),
                     lambda inspection: self.review(inspection, update_record), "Inspecting application…")

    def review(self, inspection, current=None, *, version_label="", downloaded=None):
        dialog = Adw.Dialog(title="Replace installation" if current else "Review installation",
                            content_width=560, content_height=530)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        margins(content, 18)
        group = Adw.PreferencesGroup(title="Application")
        name = Adw.EntryRow(title="Name", text=current.name if current else inspection.suggested_name)
        name.set_editable(current is None)
        version = Adw.EntryRow(title="New version (optional)" if current else "Version (optional)", text=version_label)
        group.add(name)
        group.add(version)
        content.append(group)
        version_guidance = None
        if current:
            content.append(label(f"Current version: {current.version or 'Unknown'}"))
            version_guidance = label("Version ordering cannot be verified when version information is missing.", "dim-label")
            content.append(version_guidance)
            content.append(label("Close this application before continuing. Its current managed files will be kept for rollback. "
                                 "External settings and user data are preserved."))
        choice_offset = int(len(inspection.candidates) > 1)
        recommended, reason = recommend_launcher(inspection)
        choices = (["Choose the main application launcher…"] if choice_offset else [])
        options = [c.path + (" — Recommended" if c.path == recommended else
                            " — Helper" if helper_reason(c.path) else "") for c in inspection.candidates]
        executable = Gtk.DropDown.new_from_strings(choices + options)
        factory = Gtk.SignalListItemFactory()
        def setup_choice(_, item):
            item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                                     max_width_chars=40))
        def bind_choice(_, item):
            text = item.get_item().get_string()
            item.get_child().set_text(text)
            item.get_child().set_tooltip_text(text)
        factory.connect("setup", setup_choice)
        factory.connect("bind", bind_choice)
        executable.set_factory(factory)
        executable.set_list_factory(factory)
        if recommended:
            executable.set_selected(next(i for i, c in enumerate(inspection.candidates) if c.path == recommended) + choice_offset)
        executable.set_enable_search(True)
        executable.set_tooltip_text("Select the app launcher; bundles may also contain helper executables")
        content.append(label("Application launcher", "heading"))
        content.append(executable)
        guidance = label(reason, "dim-label")
        content.append(guidance)
        if inspection.format == "appimage":
            content.append(label("The AppImage will be copied and made executable. Its embedded logo is used when available. "
                                 "Running it may require FUSE support on your system.", "dim-label"))
        size_description = "installed" if inspection.format == "appimage" else "unpacked"
        archive_label = str(inspection.source) if current else inspection.source.name
        destination = str(self.installer.paths.apps / current.app_id / "payload") if current else f"{self.installer.paths.apps}/<unique app ID>/payload"
        sizes = (f"{size_text(inspection.archive_size)} download\nEstimated installation size: {size_text(inspection.expanded_size)}"
                 if current else f"{size_text(inspection.archive_size)} download · {size_text(inspection.expanded_size)} {size_description}")
        info = label(f"{archive_label}\n{sizes}\n\n"
                     f"Destination: {destination}")
        info.set_selectable(True)
        content.append(info)
        warning = label("Only install software you trust. Inspection does not establish safety. "
                        "Apps run with your user permissions and are not sandboxed.")
        content.append(warning)
        buttons = Gtk.Box(spacing=12, homogeneous=True)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        install = Gtk.Button(label="Install")
        install.add_css_class("suggested-action")
        accepted = False
        if downloaded:
            dialog.connect("closed", lambda *_: self.online.discard(downloaded) if not accepted else None)
        def update_install(*_):
            index = executable.get_selected() - choice_offset
            valid = 0 <= index < len(inspection.candidates)
            helper = helper_reason(inspection.candidates[index].path) if valid else None
            guidance.set_text(helper or (reason if not valid or inspection.candidates[index].path == recommended else
                                         "Custom launcher selected. Check that this is the file the publisher recommends to start the app."))
            install.set_sensitive(bool(name.get_text().strip()) and valid and not helper)
            if current:
                known = bool(current.version and version.get_text().strip())
                install.set_label("Update" if known else "Replace installation")
                dialog.set_title("Review update" if known else "Replace installation")
                version_guidance.set_text("Version labels are provided by you; ordering is not verified." if known else
                                          "Version ordering cannot be verified when version information is missing.")
        name.connect("notify::text", update_install)
        executable.connect("notify::selected", update_install)
        version.connect("notify::text", update_install)
        update_install()
        def confirmed(*_):
            nonlocal accepted
            chosen = inspection.candidates[executable.get_selected() - choice_offset].path
            app_name, app_version = name.get_text(), version.get_text()
            accepted = True
            dialog.close()
            if current:
                self.run_job(lambda: install_download(self.installer, current, downloaded, chosen, app_version,
                             approved=True, cancel=self.cancel) if downloaded else
                             self.installer.update(current, inspection, chosen, app_version, self.cancel),
                             lambda record: self.installed(record, "updated", managed_download=downloaded is not None), "Replacing installation…")
            else:
                self.run_job(lambda: self.installer.install(inspection, chosen, app_name, app_version, self.cancel),
                             self.installed, "Installing application…")
        install.connect("clicked", confirmed)
        buttons.append(cancel)
        buttons.append(install)
        toolbar.add_bottom_bar(margins(buttons, 12))
        toolbar.set_content(Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER))
        dialog.set_child(toolbar)
        dialog.present(self)

    def installed(self, record, operation="installed", *, managed_download=False):
        self.toast(f"{record.name} {operation}")
        dialog = Adw.Dialog(title=f"Application {operation}", content_width=400)
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), 24)
        box.append(label(f"{record.name} is ready", "title-2"))
        box.append(label("The manager-owned download is cleaned after success or retained for recovery." if managed_download else
                         "Downloaded files are left untouched by rollback." if operation == "restored" else
                         "The downloaded file has been preserved."))
        if operation != "installed":
            box.append(label("The previous installation is available from Roll Back."))
        launch = Gtk.Button(label="Launch Application")
        launch.add_css_class("suggested-action")
        launch.connect("clicked", lambda *_: (dialog.close(), self.launch(record)))
        trash = Gtk.Button(label="Move Downloaded File to Trash")
        trash.connect("clicked", lambda *_: (dialog.close(), self.confirm_trash(record)))
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: dialog.close())
        source_is_managed = Path(record.source).is_relative_to(self.installer.paths.downloads)
        for button in ((launch, done) if managed_download or source_is_managed else (launch, trash, done)):
            box.append(button)
        dialog.set_child(box)
        def refreshed(result):
            self.show_inventory(result)
            dialog.present(self)
        self.run_job(lambda: self.installer.registry.inventory(), refreshed, "Refreshing applications…", False)

    def launch(self, record):
        self.run_job(lambda: self.installer.launch(record), lambda _: self.toast(f"Started {record.name}"),
                     "Launching application…", False)

    def details(self, record):
        result = self.update_results.get(record.app_id)
        source = self.installer.registry.update_sources.get(record.app_id, "Not configured")
        self.message(record.name, f"Version: {record.version or 'Unknown'}\n"
                     f"Installed: {record.installed_at}\nSize: {size_text(record.installed_size)}\n\n"
                     f"Location: {record.install_dir}\nExecutable: {record.executable}\n\n"
                     f"Original file: {record.source}\nSHA-256: {record.checksum}\n\n"
                     f"Update source: {source}\nUpdate status: {result.status if result else 'Not checked'}\n"
                     f"{result.detail if result else 'Use Check for Updates to request a check.'}\n\n"
                     "Uninstall preserves application settings and user data outside the managed bundle.")

    def confirm_uninstall(self, record):
        dialog = Adw.AlertDialog(heading=f"Uninstall {record.name}?",
                                body="This removes the managed application, its retained rollback version, and its shortcut. Settings and data outside the managed bundles are preserved.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("uninstall", "Uninstall")
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def response(_, chosen):
            if chosen == "uninstall":
                self.run_job(lambda: self.installer.uninstall(record),
                             lambda _: (self.toast(f"{record.name} uninstalled"), self.refresh()),
                             "Removing application…", False)
        dialog.connect("response", response)
        dialog.present(self)

    def confirm_rollback(self, record):
        previous = self.installer.registry.rollback_versions.get(record.app_id)
        target = previous.version if previous and previous.version else "Unknown"
        dialog = Adw.AlertDialog(heading=f"Roll back {record.name}?",
                                body=f"Restore version: {target}\n\nClose the application first. "
                                     "The current installation will become the rollback version. "
                                     "External settings and user data are not reverted.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rollback", "Roll Back")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: self.run_job(
            lambda: self.installer.rollback(record), lambda restored: self.installed(restored, "restored"),
            "Restoring previous installation…", False) if response == "rollback" else None)
        dialog.present(self)

    def confirm_trash(self, record):
        dialog = Adw.AlertDialog(heading="Move downloaded file to Trash?", body=record.source)
        dialog.add_response("cancel", "Keep File")
        dialog.add_response("trash", "Move to Trash")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def response(_, chosen):
            if chosen == "trash":
                self.run_job(lambda: trash_source(record), lambda _: self.toast("File moved to Trash"), "Moving file to Trash…", False)
        dialog.connect("response", response)
        dialog.present(self)

    def about(self, *_):
        Adw.AboutDialog(application_name="TidyLinux", application_icon="io.github.tidylinux.TidyLinux",
                        version=__version__, website="https://github.com/Ishank307/Tidy_Linux",
                        license_type=Gtk.License.GPL_3_0, comments="Manage portable applications and installation leftovers.\nInstall clean. Stay organized.").present(self)
