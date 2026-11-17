#!/usr/bin/env python3
"""Explicit, user-only setup/removal of the TidyLinux checkout launcher."""
import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from tidylinux.desktop import desktop_exec, desktop_string, exec_argument
from tidylinux.registry import Paths
from tidylinux.models import TidyError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    options = parser.parse_args()
    paths = Paths.default()
    path = paths.desktops / "io.github.tidylinux.TidyLinux.desktop"
    icon = PROJECT / "data/icons/io.github.tidylinux.TidyLinux.svg"
    content = ("[Desktop Entry]\nType=Application\nVersion=1.0\nName=TidyLinux\n"
               "Comment=Install clean. Stay organized.\n"
               f"Exec={desktop_exec(sys.executable)} {exec_argument(str(PROJECT / 'scripts/run.py'))}\n"
               f"Icon={desktop_string(str(icon))}\nTerminal=false\nCategories=System;Utility;\n"
               "StartupNotify=true\nX-TidyLinux-Launcher=1\n")
    try:
        if paths.desktops.is_symlink() or path.is_symlink():
            raise TidyError("Launcher directory or file is a symbolic link; left untouched.")
        if path.exists():
            if not path.is_file() or path.stat().st_size > 65536 or path.read_text() != content:
                raise TidyError("An existing launcher differs from this checkout; left untouched.")
            if options.action == "uninstall":
                path.unlink()
                print(f"Removed {path}. Installed applications and data were preserved.")
            else:
                print(f"Already installed: {path}")
        elif options.action == "install":
            paths.desktops.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            print(f"Installed {path}. Keep this checkout at {PROJECT}.")
        else:
            print("No TidyLinux launcher to remove.")
    except (OSError, TidyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
