# Installation

## A. Fedora RPM (recommended for ordinary users)

V1.4 has not been published. Do not assume a downloadable RPM exists yet. The [GitHub Releases page](https://github.com/Ishank307/Tidy_Linux/releases) is the intended publication location after maintainer approval; this project is not in the official Fedora repositories.

The current validation host is Fedora 45, Python 3.15, GTK 4.24, Libadwaita 1.10. Other Fedora releases have not been tested. Runtime minimums are Python 3.11, GTK 4.10, and Libadwaita 1.5. Use a supported Fedora release with those libraries; version minimums alone are not proof of compatibility. Atomic desktop RPM layering has not been tested.

After an actual release is published, download the RPM for your Fedora release and its matching `.sha256` file into the same directory. Substitute the real filename for the explicitly marked shell placeholder:

```bash
RPM_FILE='REPLACE_WITH_DOWNLOADED_RPM_FILENAME.rpm'
sha256sum -c "$RPM_FILE.sha256"
sudo dnf install "./$RPM_FILE"
```

A checksum detects corruption relative to the checksum file; it does not authenticate a publisher if both files were compromised. The development RPM is unsigned. DNF resolves Python, GTK, Libadwaita, and PyGObject dependencies from your configured Fedora repositories. Do not bypass dependency failures with `--nodeps`.

Open GNOME Applications and search for TidyLinux, or run:

```bash
tidylinux
```

Upgrade by downloading and verifying a newer package, then running `sudo dnf upgrade "./$RPM_FILE"` with the variable set to that new file. Remove the manager with:

```bash
sudo dnf remove tidylinux
```

Managed application bundles, rollback versions, update sources, downloads, and per-app desktop shortcuts remain in your user XDG storage. External application settings remain too. The RPM has no application lifecycle scriptlets and owns no home-directory files. Use TidyLinux's application Uninstall action before removing the manager if you want those bundles removed. Reinstalling the manager makes retained bundles visible again.

If installation fails, check the package matches your Fedora environment, retry after fixing repository/network errors, and inspect `rpm -qp --requires "$RPM_FILE"`. Never run TidyLinux with sudo.

## B. Source development installation

```bash
sudo dnf install git python3 python3-gobject gtk4 libadwaita python3-pytest desktop-file-utils appstream
git clone https://github.com/Ishank307/Tidy_Linux.git
cd Tidy_Linux
python3 scripts/run.py
```

No global pip install is needed. For optional developer isolation, `python3 -m venv --system-site-packages .venv` exposes system PyGObject. Install pytest through Fedora as above, then use `.venv/bin/python -m pytest -q`.

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/smoke_gui.py
```

A graphical session is needed for the smoke test; exit 77 means no display and is not a pass. Optional source launcher setup: `python3 scripts/desktop_launcher.py install`. Keep the checkout in place. Remove that launcher with `python3 scripts/desktop_launcher.py uninstall` before switching to the RPM, to avoid a user launcher shadowing the system entry. Differing launchers are preserved for manual review.

For reproducible RPM source/build commands, see [Fedora packaging](../packaging/fedora/README.md).

## Local 1.4 development RPM

The build produces **`tidylinux-1.4.0-1.fc45.noarch.rpm`** on the Fedora 45 validation host. This is an unsigned, unpublished test artifact, not an assertion of a public release. After building, change to the exact `RPMS/noarch` directory printed under the build output, then:

```bash
sha256sum -c tidylinux-1.4.0-1.fc45.noarch.rpm.sha256
sudo dnf install ./tidylinux-1.4.0-1.fc45.noarch.rpm
tidylinux
```

Use a disposable Fedora VM for initial testing. Installing on your normal desktop is your explicit choice; the build scripts never do it.
