"""Small native interface. Blocking filesystem work stays on one worker thread."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import logging

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .archive import inspect_archive
from .installer import Installer, trash_source
from .models import Cancelled

LOG = logging.getLogger(__name__)


def margins(widget, amount=18):
    for side in ("top", "bottom", "start", "end"):
        getattr(widget, f"set_margin_{side}")(amount)
    return widget


def label(text, css=None):
    result = Gtk.Label(label=text, wrap=True, xalign=0)
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
        menu.append("About TidyLinux", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        for name, callback in (("install", self.choose_archive), ("about", self.about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        layout.append(header)
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
            self.cancel.set()
            self.toast("Cancelling the current operation. Close again when it finishes.")
            return True
        self.pool.shutdown(wait=False, cancel_futures=True)
        return False

    def toast(self, text):
        self.overlay.add_toast(Adw.Toast(title=text, timeout=5))

    def message(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=str(body))
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def run_job(self, work, done, title, cancellable=True):
        if self.busy:
            self.toast("Wait for the current operation to finish.")
            return
        self.busy = True
        self.cancel = Event()
        self.install_button.set_sensitive(False)
        dialog = Adw.Dialog(title=title, content_width=340, can_close=False)
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18), 24)
        box.append(label(title, "title-3"))
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        box.append(spinner)
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
            try:
                result = future.result()
            except Cancelled:
                self.toast("Operation cancelled")
            except Exception as exc:
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
        while child := self.rows.get_first_child():
            self.rows.remove(child)
        for record in records:
            row = Adw.ActionRow(title=GLib.markup_escape_text(record.name),
                                subtitle=GLib.markup_escape_text(" · ".join(filter(None, (record.version, size_text(record.installed_size))))))
            image = Gtk.Image(icon_name="application-x-executable-symbolic", pixel_size=32)
            if record.icon and Path(record.icon).is_file():
                image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(record.icon)))
            row.add_prefix(image)
            launch = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=f"Launch {record.name}")
            launch.add_css_class("flat")
            launch.connect("clicked", lambda _, r=record: self.launch(r))
            row.add_suffix(launch)
            popover = Gtk.Popover()
            actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for text, callback in (("Details", self.details), ("Uninstall…", self.confirm_uninstall)):
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

    def choose_archive(self, *_):
        if self.busy:
            return
        chooser = Gtk.FileDialog(title="Choose an application archive or AppImage")
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
                    self.inspect(Path(file.get_path()))
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

    def inspect(self, path):
        self.run_job(lambda: inspect_archive(path, cancel=self.cancel), self.review, "Inspecting application…")

    def review(self, inspection):
        dialog = Adw.Dialog(title="Review installation", content_width=560, content_height=530)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        margins(content, 18)
        group = Adw.PreferencesGroup(title="Application")
        name = Adw.EntryRow(title="Name", text=inspection.suggested_name)
        version = Adw.EntryRow(title="Version (optional)")
        group.add(name)
        group.add(version)
        content.append(group)
        choice_offset = int(len(inspection.candidates) > 1)
        choices = (["Choose the main application launcher…"] if choice_offset else [])
        executable = Gtk.DropDown.new_from_strings(choices + [c.path for c in inspection.candidates])
        executable.set_enable_search(True)
        executable.set_tooltip_text("Select the app launcher; bundles may also contain helper executables")
        content.append(label("Executable", "heading"))
        content.append(executable)
        if len(inspection.candidates) > 1:
            content.append(label("Several executables were found. Choose the application's main launcher.", "dim-label"))
        if inspection.format == "appimage":
            content.append(label("The AppImage will be copied and made executable. Its embedded logo is used when available. "
                                 "Running it may require FUSE support on your system.", "dim-label"))
        size_description = "installed" if inspection.format == "appimage" else "unpacked"
        info = label(f"{inspection.source.name}\n{size_text(inspection.archive_size)} download · "
                     f"{size_text(inspection.expanded_size)} {size_description}\n\n"
                     f"Destination: {self.installer.paths.apps}/<unique app ID>/payload")
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
        def update_install(*_):
            install.set_sensitive(bool(name.get_text().strip()) and
                                  choice_offset <= executable.get_selected() < len(inspection.candidates) + choice_offset)
        name.connect("notify::text", update_install)
        executable.connect("notify::selected", update_install)
        update_install()
        def confirmed(*_):
            chosen = inspection.candidates[executable.get_selected() - choice_offset].path
            app_name, app_version = name.get_text(), version.get_text()
            dialog.close()
            self.run_job(lambda: self.installer.install(inspection, chosen, app_name, app_version, self.cancel),
                         self.installed, "Installing application…")
        install.connect("clicked", confirmed)
        buttons.append(cancel)
        buttons.append(install)
        content.append(buttons)
        toolbar.set_content(Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER))
        dialog.set_child(toolbar)
        dialog.present(self)

    def installed(self, record):
        self.toast(f"{record.name} installed")
        dialog = Adw.Dialog(title="Application installed", content_width=400)
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), 24)
        box.append(label(f"{record.name} is ready", "title-2"))
        box.append(label("The downloaded file has been preserved."))
        launch = Gtk.Button(label="Launch Application")
        launch.add_css_class("suggested-action")
        launch.connect("clicked", lambda *_: (dialog.close(), self.launch(record)))
        trash = Gtk.Button(label="Move Downloaded File to Trash")
        trash.connect("clicked", lambda *_: (dialog.close(), self.confirm_trash(record)))
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: dialog.close())
        for button in (launch, trash, done):
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
        self.message(record.name, f"Version: {record.version or 'Unknown'}\n"
                     f"Installed: {record.installed_at}\nSize: {size_text(record.installed_size)}\n\n"
                     f"Location: {record.install_dir}\nExecutable: {record.executable}\n\n"
                     f"Original file: {record.source}\nSHA-256: {record.checksum}\n\n"
                     "Uninstall preserves application settings and user data outside the managed bundle.")

    def confirm_uninstall(self, record):
        dialog = Adw.AlertDialog(heading=f"Uninstall {record.name}?",
                                body="This removes the managed application and its shortcut. Settings and data outside the managed bundle are preserved.")
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
        Adw.AboutDialog(application_name="TidyLinux", application_icon="package-x-generic",
                        version="1.0.0", developer_name="TidyLinux contributors",
                        license_type=Gtk.License.GPL_3_0, comments="Install clean. Stay organized.").present(self)
