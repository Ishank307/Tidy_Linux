"""Desktop entry encoding, with no shell interpolation."""
import hashlib
from pathlib import Path

from .models import AppRecord, TidyError


def desktop_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def exec_argument(value: str) -> str:
    # Desktop Exec has two escaping layers, plus field-code expansion.
    quoted = value.replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        quoted = quoted.replace(character, "\\" + character)
    return desktop_string('"' + quoted + '"')


def desktop_content(record: AppRecord) -> str:
    executable = str(Path(record.install_dir) / record.executable)
    # Working beside the launcher preserves bundles' relative resources.
    lines = ["[Desktop Entry]", "Type=Application", "Version=1.0",
             f"Name={desktop_string(record.name)}", f"Exec={exec_argument(executable)}",
             f"Path={desktop_string(str(Path(executable).parent))}", "Terminal=false",
             "Categories=Utility;", "StartupNotify=false", f"X-TidyLinux-ID={record.app_id}"]
    lines.append("Icon=" + desktop_string(record.icon or "application-x-executable"))
    return "\n".join(lines) + "\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_desktop(record: AppRecord) -> None:
    path = Path(record.desktop_path)
    if path.is_symlink():
        raise TidyError("Desktop entry is a symbolic link; refusing to remove it.")
    if path.exists() and (not path.is_file() or path.stat().st_size > 65536
                          or digest(path.read_bytes()) != record.desktop_checksum):
        raise TidyError("Desktop entry was changed outside TidyLinux. Restore or remove it before uninstalling.")
