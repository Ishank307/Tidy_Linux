#!/usr/bin/env python3
"""Activate installed code under a virtual display; close after inventory settles."""
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gio
import tidylinux
from tidylinux.app import TidyApplication

assert Path(tidylinux.__file__).is_relative_to('/usr/lib'), tidylinux.__file__
assert os.getuid() != 0
app = TidyApplication()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
started = time.monotonic()
passed = []


def finish():
    window = app.get_active_window()
    if time.monotonic() - started > 15:
        app.quit()
        return False
    if window is None or window.busy or not window.get_mapped():
        return True
    assert len(window.records) == 1
    assert window.get_width() > 0
    assert window.records[0].version == '2'
    passed.append(True)
    window.close()
    return False


with patch('tidylinux.updates.build_opener', side_effect=AssertionError('Startup network forbidden')), \
     patch('tidylinux.cleanup.Cleanup.scan', side_effect=AssertionError('Startup scan forbidden')):
    GLib.timeout_add(100, finish)  # Test harness only, not application behavior.
    result = app.run([sys.argv[0]])
assert passed and result == 0
print(f'PASS: installed GUI {tidylinux.__version__}, mapped window, fixture inventory, no startup network/scan')
