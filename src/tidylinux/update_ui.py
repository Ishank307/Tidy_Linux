"""Compact explicit check → asset review → download → installation review UI."""
from gi.repository import Adw, GLib, Gtk, Pango

from .models import TidyError
from .sources import SourceStore, repository_url
from .updates import (CheckResult, GitHubClient, check_installed, discard_download)


class OnlineUpdates:
    def __init__(self, window):
        self.window = window
        self.sources = SourceStore(window.installer.paths)
        self.client = GitHubClient()  # No opener or network request is constructed here.

    def check(self, *_):
        window = self.window
        if window.busy or not window.records:
            return
        records = list(window.records)
        window.update_results = {record.app_id: CheckResult(record,
            "Checking" if record.app_id in window.installer.registry.update_sources else "Not configured", "")
            for record in records}
        window.show_inventory((records, window.inventory_warnings))
        def completed(results):
            window.update_results = {result.record.app_id: result for result in results}
            window.show_inventory((window.records, window.inventory_warnings))
            window.toast("Update check finished. Review the application statuses.")
        def failed(_):
            window.update_results = {}
            window.show_inventory((window.records, window.inventory_warnings))
        window.run_job(lambda: check_installed(records, self.sources, self.client, requested=True,
                       cancel=window.cancel, progress=window.report_progress), completed,
                       "Checking for updates…", progress=True, on_failure=failed)

    def configure(self, record):
        from .window import label, margins
        window = self.window
        if window.busy:
            return
        current = window.installer.registry.update_sources.get(record.app_id)
        dialog = Adw.Dialog(title="Update source", content_width=500)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16))
        box.append(label(record.name, "title-3"))
        group = Adw.PreferencesGroup()
        entry = Adw.EntryRow(title="GitHub repository", text=current or "")
        group.add(entry)
        box.append(group)
        box.append(label("Use https://github.com/OWNER/REPOSITORY. You must verify that the publisher owns this repository. "
                         "TidyLinux does not establish that it is official. Linking does not check for releases."))
        validation = label("", "dim-label")
        box.append(validation)
        buttons = margins(Gtk.Box(spacing=12, homogeneous=True), 12)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        link = Gtk.Button(label="Link Source")
        link.add_css_class("suggested-action")
        def validate(*_):
            try:
                repository_url(entry.get_text())
            except TidyError as exc:
                link.set_sensitive(False)
                validation.set_text(str(exc))
            else:
                link.set_sensitive(True)
                validation.set_text("A separate confirmation is required. No network request will be made.")
        def confirm(url):
            dialog.close()
            confirmation = Adw.AlertDialog(heading="Remove update source?" if url is None else "Link this update source?",
                body=(f"{record.name}\n{url}\n\nOnly continue if you trust this repository as the publisher's release source. "
                      "GitHub hosting alone does not establish authenticity." if url else
                      f"{record.name} will remain installed and usable. Manual release checks will no longer contact its source."))
            confirmation.add_response("cancel", "Cancel")
            confirmation.add_response("confirm", "Remove Source" if url is None else "Link Source")
            confirmation.set_default_response("cancel")
            confirmation.set_close_response("cancel")
            def response(_, choice):
                if choice == "confirm":
                    def saved(_):
                        window.update_results.pop(record.app_id, None)
                        window.refresh()
                    window.run_job(lambda: self.sources.configure(record, url, confirmed=True), saved,
                                   "Saving update source…", False)
            confirmation.connect("response", response)
            confirmation.present(window)
        link.connect("clicked", lambda *_: confirm(repository_url(entry.get_text())))
        entry.connect("notify::text", validate)
        validate()
        if current:
            remove = Gtk.Button(label="Remove Source")
            remove.connect("clicked", lambda *_: confirm(None))
            box.append(remove)
        buttons.append(cancel)
        buttons.append(link)
        toolbar.set_content(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_bottom_bar(buttons)
        dialog.set_child(toolbar)
        dialog.present(window)

    def review_release(self, result):
        from .window import label, margins
        window, release = self.window, result.release
        if window.busy or release is None or not release.assets:
            return
        dialog = Adw.Dialog(title="Review release download", content_width=540, content_height=480)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16))
        box.append(label(result.record.name, "title-3"))
        box.append(label(f"Installed version: {result.record.version or 'Unknown'}\nRelease label: {release.tag}\n{release.repository}"))
        box.append(label(result.detail))
        box.append(label("Choose the exact asset to download. Filenames do not prove compatibility. "
                         "Verify the publisher, Linux architecture, and runtime. Nothing will be installed by downloading."))
        selector = Gtk.DropDown.new_from_strings(["Choose a release asset…"] + [asset.name for asset in release.assets])
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _, item: item.set_child(Gtk.Label(xalign=0,
                        ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=35)))
        def bind(_, item):
            text = item.get_item().get_string()
            item.get_child().set_text(text)
            item.get_child().set_tooltip_text(text)
        factory.connect("bind", bind)
        selector.set_factory(factory)
        selector.set_list_factory(factory)
        selector.set_enable_search(True)
        box.append(selector)
        asset_info = label("Select an asset. There is no automatic selection.")
        box.append(asset_info)
        box.append(label("You linked this repository. GitHub hosting and a checksum do not establish publisher authenticity."))
        buttons = margins(Gtk.Box(spacing=12, homogeneous=True), 12)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        download = Gtk.Button(label="Download for review", sensitive=False)
        download.add_css_class("suggested-action")
        def selected(*_):
            index = selector.get_selected() - 1
            valid = 0 <= index < len(release.assets)
            download.set_sensitive(valid)
            if valid:
                asset = release.assets[index]
                checksum = f"GitHub SHA-256: {asset.sha256}" if asset.sha256 else "No SHA-256 supplied by GitHub. Only size and archive validation will be checked."
                asset_info.set_text(f"{asset.name}\n{GLib.format_size(asset.size)}\n{asset.compatibility}\n{checksum}")
            else:
                asset_info.set_text("Select an asset. There is no automatic selection.")
        selector.connect("notify::selected", selected)
        def confirmed(*_):
            index = selector.get_selected() - 1
            if not 0 <= index < len(release.assets):
                return
            asset = release.assets[index]
            dialog.close()
            window.run_job(lambda: self.client.download(window.installer.paths, result.record, release, asset,
                           confirmed=True, cancel=window.cancel, progress=window.report_progress),
                           lambda downloaded: window.review(downloaded.inspection, result.record,
                               version_label=release.tag, downloaded=downloaded),
                           "Downloading release asset…", progress=True)
        download.connect("clicked", confirmed)
        buttons.append(cancel)
        buttons.append(download)
        toolbar.set_content(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_bottom_bar(buttons)
        dialog.set_child(toolbar)
        dialog.present(window)

    def discard(self, downloaded):
        if self.window.closing:
            return  # Interrupted sessions remain private and are reported at startup.
        self.window.run_job(lambda: discard_download(self.window.installer.paths, downloaded.directory),
                            lambda _: None, "Discarding temporary download…", False)
