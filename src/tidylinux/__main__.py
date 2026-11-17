"""Run with python3 -m tidylinux from an installed/editable checkout."""
import logging
import sys


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        from .app import TidyApplication
    except (ImportError, ValueError) as exc:
        print(f"TidyLinux requires system GTK4, Libadwaita and PyGObject: {exc}\n"
              "Fedora: sudo dnf install python3-gobject gtk4 libadwaita", file=sys.stderr)
        return 1
    return TidyApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
