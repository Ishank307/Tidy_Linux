#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in rpmbuild rpm python3 desktop-file-validate appstreamcli sha256sum; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
for package in python3-devel pyproject-rpm-macros python3-setuptools python3-wheel python3-pytest desktop-file-utils appstream; do
    rpm -q "$package" >/dev/null || { echo "Missing build dependency: $package (see packaging/fedora/README.md)" >&2; exit 1; }
done
mkdir -p dist
build_dir=$(mktemp -d "$PWD/dist/rpmbuild.XXXXXXXX")
mkdir -p "$build_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
version=$(PYTHONPATH=scripts python3 -c 'from source_archive import version; print(version())')
python3 scripts/source_archive.py "$build_dir/SOURCES/tidylinux-$version.tar.gz"
cp packaging/fedora/tidylinux.spec "$build_dir/SPECS/"
rpmbuild --define "_topdir $build_dir" -ba "$build_dir/SPECS/tidylinux.spec"
mapfile -t packages < <(find "$build_dir/RPMS" -name '*.rpm' -type f)
((${#packages[@]})) || { echo 'No binary RPM produced' >&2; exit 1; }
for package in "${packages[@]}"; do bash scripts/verify_release.sh "$package"; done
for source_rpm in "$build_dir"/SRPMS/*.src.rpm; do
    (
        cd "$(dirname "$source_rpm")"
        sha256sum "$(basename "$source_rpm")" > "$(basename "$source_rpm").sha256"
        sha256sum -c "$(basename "$source_rpm").sha256"
    )
done
printf 'Build output: %s\n'  "$build_dir"
