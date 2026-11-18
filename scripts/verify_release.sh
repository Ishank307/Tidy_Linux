#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 && -f $1 ]] || { echo "Usage: $0 path/to/tidylinux.rpm" >&2; exit 1; }
package=$(realpath "$1")
rpm -K --nosignature "$package"
rpm -qip "$package"
rpm -qp --requires "$package"
[[ $(rpm -qp --qf '%{NAME} %{ARCH}' "$package") == 'tidylinux noarch' ]]
files=$(rpm -qpl "$package")
for expected in /usr/bin/tidylinux /usr/share/applications/io.github.tidylinux.TidyLinux.desktop /usr/share/icons/hicolor/scalable/apps/io.github.tidylinux.TidyLinux.svg /usr/share/metainfo/io.github.tidylinux.TidyLinux.metainfo.xml; do
    grep -Fx "$expected" <<<"$files" >/dev/null || { echo "Missing: $expected" >&2; exit 1; }
done
if grep -E '^/(home|root|var|etc)/' <<<"$files"; then echo 'Unexpected state/config payload' >&2; exit 1; fi
[[ -z $(rpm -qp --scripts "$package") ]] || { echo 'Unexpected RPM lifecycle scriptlets' >&2; exit 1; }
printf '%s\n' "$files"
(
    cd "$(dirname "$package")"
    sha256sum "$(basename "$package")" > "$(basename "$package").sha256"
    sha256sum -c "$(basename "$package").sha256"
)
stat --printf='Artifact: %n\nSize: %s bytes\n' "$package"
