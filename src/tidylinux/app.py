"""Native application lifecycle."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from .window import MainWindow


class TidyApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.tidylinux.TidyLinux",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.set_accels_for_action("win.install", ["<Primary>o"])
        self.set_accels_for_action("app.quit", ["<Primary>q", "<Primary>w"])
        self.set_accels_for_action("win.check-updates", ["<Primary>r"])
        self.set_accels_for_action("win.cleanup", ["<Primary><Shift>c"])
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self.quit_requested)
        self.add_action(quit_action)

    def quit_requested(self, *_):
        window = self.get_active_window()
        if window:
            window.close()

    def do_activate(self):
        window = self.get_active_window() or MainWindow(self)
        window.present()
