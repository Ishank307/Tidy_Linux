"""Read AppImage artwork without running or mounting the downloaded executable."""
import logging
import os
from pathlib import Path
import selectors
import shutil
import struct
import subprocess
import time

from .archive import check_cancel

LOG = logging.getLogger(__name__)
ICON_LIMIT = 4 * 1024 * 1024


def squashfs_offset(path: Path) -> int | None:
    # Bound the runtime scan; validate the superblock rather than matching the
    # magic string embedded in the runtime's own diagnostic code.
    with path.open("rb") as source:
        data = source.read(16 * 1024 * 1024)
    size = path.stat().st_size
    offset = 0
    while (offset := data.find(b"hsqs", offset)) >= 0:
        if offset + 96 <= len(data):
            block = struct.unpack_from("<I", data, offset + 12)[0]
            version = struct.unpack_from("<HH", data, offset + 28)
            used = struct.unpack_from("<Q", data, offset + 40)[0]
            if version == (4, 0) and 4096 <= block <= 1024 * 1024 and block & (block - 1) == 0 and 96 <= used <= size - offset:
                return offset
        offset += 4
    return None


def read_icon(path: Path, cancel=None) -> tuple[bytes, str] | None:
    """Read only the standard .DirIcon through unsquashfs, with bounded output.

    The tool resolves links inside the image; no archive paths are written to
    disk. Missing artwork/tools and unsupported images retain the generic icon.
    """
    check_cancel(cancel)
    tool = shutil.which("unsquashfs")
    if not tool:
        return None
    offset = squashfs_offset(path)
    if offset is None:
        return None
    process = subprocess.Popen([tool, "-o", str(offset), "-cat", str(path), ".DirIcon"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    data = bytearray()
    deadline = time.monotonic() + 10
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    return None
                if not selector.select(0.1):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > ICON_LIMIT:
                    return None
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            return None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    content = bytes(data)
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return content, ".png"
    if content.lstrip().startswith((b"<svg", b"<?xml")) and b"<svg" in content[:4096]:
        return content, ".svg"
    if content.lstrip().startswith(b"/* XPM */"):
        return content, ".xpm"
    return None


def install_icon(path: Path, destination: Path, cancel=None) -> str | None:
    try:
        result = read_icon(path, cancel)
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Could not read AppImage icon: %s", exc)
        return None
    if result is None:
        return None
    data, suffix = result
    name = ".tidylinux-icon" + suffix
    target = destination / name
    with target.open("xb") as stream:
        stream.write(data)
    target.chmod(0o600)
    return name
