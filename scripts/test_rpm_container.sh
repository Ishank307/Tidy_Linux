#!/usr/bin/env bash
# Explicit container lifecycle acceptance; never installs onto the host.
set -euo pipefail
[[ $# == 2 ]] || { echo "Usage: $0 older.rpm candidate.rpm" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
old=$(realpath "$1")
new=$(realpath "$2")
podman run --rm --security-opt label=disable \
    -v "$repo:/repo:ro" -v "$old:/old.rpm:ro" -v "$new:/new.rpm:ro" \
    registry.fedoraproject.org/fedora:45 bash -euxo pipefail -c '
    dnf install -y python3 python3-gobject gtk4 libadwaita desktop-file-utils shadow-utils util-linux xorg-x11-server-Xvfb xorg-x11-xauth dbus-daemon
    useradd -m tidytest
    runuser -u tidytest -- env HOME=/home/tidytest PYTHONPATH=/repo/src TIDYLINUX_CONTAINER_TEST=1 python3 /repo/scripts/rpm_lifecycle_fixture.py seed
    dnf install -y /old.rpm
    runuser -u tidytest -- env HOME=/home/tidytest TIDYLINUX_CONTAINER_TEST=1 python3 /repo/scripts/rpm_lifecycle_fixture.py check
    dnf upgrade -y /new.rpm
    runuser -u tidytest -- env HOME=/home/tidytest TIDYLINUX_CONTAINER_TEST=1 python3 /repo/scripts/rpm_lifecycle_fixture.py check
    tidylinux --help
    runuser -u tidytest -- env HOME=/home/tidytest XDG_DATA_HOME=/home/tidytest/fixture/data GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a python3 /repo/scripts/installed_gui_smoke.py
    dnf remove -y tidylinux
    runuser -u tidytest -- env HOME=/home/tidytest TIDYLINUX_CONTAINER_TEST=1 python3 /repo/scripts/rpm_lifecycle_fixture.py check
    test ! -e /usr/bin/tidylinux
    echo "PASS: isolated DNF install, upgrade, installed GUI launch, removal and data preservation"
    '
