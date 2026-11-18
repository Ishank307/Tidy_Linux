"""Native, explicitly initiated cleanup review."""
from pathlib import Path
from gi.repository import Adw, Gio, GLib, Gtk
from .cleanup import Cleanup


def present_cleanup(window):
    from .window import label, margins
    if window.busy:
        return
    backend = Cleanup(window.installer.paths)
    dialog = Adw.Dialog(title='Cleanup', content_width=600, content_height=480)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12))
    location = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or Path.home() / 'Downloads')
    location_label = label(str(location))
    box.append(location_label)
    box.append(label('Scan reviews installer files directly in this folder. Nothing is selected automatically.'))
    actions = Gtk.Box(spacing=8)
    choose = Gtk.Button(label='Location…')
    scan = Gtk.Button(label='Scan')
    folder = Gtk.Button(label='Inspect Folder…')
    for button in (choose, scan, folder):
        actions.append(button)
    box.append(actions)
    summary = label('No scan performed.')
    box.append(summary)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(rows)
    selections = []
    trash = Gtk.Button(label='Move Selected to Trash', sensitive=False)
    def show(result):
        items, warnings = result
        while rows.get_first_child():
            rows.remove(rows.get_first_child())
        selections.clear()
        summary.set_text(f'{len(items)} reviewable items · {GLib.format_size(sum(i.size for i in items))}\n' + '\n'.join(warnings))
        for item in items:
            check = Gtk.CheckButton()
            check.set_child(label(f'{item.path.name} · {GLib.format_size(item.size)}\n{item.path.parent}\n{item.category}\n{item.reason}\nTidyLinux owned: {"Yes" if item.owned else "No"}'))
            check.connect('toggled', lambda *_: trash.set_sensitive(any(c.get_active() for c, _ in selections)))
            selections.append((check, item))
            rows.append(check)
        trash.set_sensitive(False)
    def pick(manual=False):
        picker = Gtk.FileDialog(title='Inspect unverified folder' if manual else 'Choose scan location')
        def selected(picker, result):
            nonlocal location
            try:
                selected_path = Path(picker.select_folder_finish(result).get_path())
            except (GLib.Error, TypeError):
                return
            if manual:
                window.run_job(lambda: ([backend.inspect_folder(selected_path, requested=True, cancel=window.cancel)], []), show,
                               'Inspecting folder…', progress=True)
            else:
                location = selected_path
                location_label.set_text(str(location))
                show(([], ['Location changed. Press Scan to inspect.']))
        picker.select_folder(window, None, selected)
    choose.connect('clicked', lambda *_: pick())
    folder.connect('clicked', lambda *_: pick(True))
    scan.connect('clicked', lambda *_: window.run_job(lambda: backend.scan(location, requested=True,
        cancel=window.cancel, progress=window.report_progress), show, 'Scanning cleanup candidates…', progress=True))
    def confirm(*_):
        chosen = [item for check, item in selections if check.get_active()]
        if not chosen or window.busy:
            return
        def execute(folder_approved=False):
            window.run_job(lambda: backend.trash(chosen, confirmed=True, folder_confirmed=folder_approved,
                cancel=window.cancel), lambda results: show(([], results)), 'Moving selected items to Trash…', progress=True)
        def ask(heading, body, callback):
            alert = Adw.AlertDialog(heading=heading, body=body)
            alert.add_response('cancel', 'Cancel')
            alert.add_response('trash', 'Move to Trash')
            alert.set_response_appearance('trash', Adw.ResponseAppearance.DESTRUCTIVE)
            alert.set_default_response('cancel')
            alert.set_close_response('cancel')
            alert.connect('response', lambda _, response: callback() if response == 'trash' else None)
            alert.present(window)
        def approved():
            if any(i.category == 'Manually selected folder for review' for i in chosen):
                ask('Move unverified folders to Trash?', 'These folders may contain external applications or personal data. Confirm that you reviewed their contents.\n' + '\n'.join(str(i.path) for i in chosen), lambda: execute(True))
            else:
                execute()
        ask('Move selected items to Trash?', '\n'.join(str(i.path) for i in chosen) + '\nTrash does not necessarily reclaim disk space.', approved)
    trash.connect('clicked', confirm)
    toolbar.set_content(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
    toolbar.add_bottom_bar(margins(trash, 12))
    dialog.set_child(toolbar)
    dialog.present(window)
