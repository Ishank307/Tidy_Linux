# TidyLinux

**Install clean. Stay organized.**

A small native GTK4/Libadwaita application for installing portable Linux apps from local archives and AppImages. Select or drop a file, review its launcher and name, install, then launch it from TidyLinux or GNOME. Installs belong to your user; root access is unnecessary.

For a summary of delivered features, validation evidence, and remaining work, see the [V1 achievement overview](docs/V1_OVERVIEW.md).
The [V1 reliability audit](docs/V1_RELIABILITY.md) records fixes, verified results, and an isolated manual testing checklist.
For local application updates, rollback, and recovery behavior, see the [V1.1 release notes](docs/V1_1_RELEASE_NOTES.md).

## Fedora setup and launch

Requires Python 3.11+, GTK 4.10+, Libadwaita 1.5+, and PyGObject. Development was checked on Fedora 45 with Python 3.15.2, GTK 4.24 and Libadwaita 1.10. System libraries come from Fedora; no global pip installation is needed.

Install dependencies yourself if missing:

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita python3-pytest
```

For embedded AppImage logos, also install `squashfs-tools` (`sudo dnf install squashfs-tools`). TidyLinux reads the standard `.DirIcon` from type 2 AppImages without executing or mounting them. It saves a private icon file and uses it in both the installed list and the desktop search shortcut. Icon reads are limited to 4 MiB and 10 seconds. Missing tools, unsupported images and missing or unrecognized logos fall back to the generic application icon.

From the project directory, launch immediately:

```bash
python3 scripts/run.py
```

Or use the module entry point from a source checkout:

```bash
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m tidylinux
```

For an editable development installation and isolated test tools:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e . pytest
python -m tidylinux
python -m pytest -q
```

`--system-site-packages` exposes Fedora's PyGObject to the environment. Do not pip-install GTK bindings. There is no runtime network access, background service, telemetry, or cloud dependency.

## Use

- Choose **Install App** (Ctrl+O), **Choose App**, or drop a local archive or `.AppImage` into the window.
- Inspect the review, check the suggested launcher, and edit the name or optional version. Recommendations use filenames and bundle layout; ambiguous bundles require an explicit choice. Known helper selections disable Install and are also rejected by the installer. For VS Code, the main launcher is `VSCode-linux-x64/code`; `chrome-sandbox` and `chrome_crashpad_handler` are helpers. Candidates include Linux ELF files and recognizable executable shebang scripts. Inspection never runs archive contents. Long launcher paths are shortened visually, with the full path in a tooltip; Cancel and Install stay visible when the review scrolls.
- Confirm **Install**. The worker thread validates a private snapshot against the reviewed SHA-256 and extracts into private staging. Cancellation rolls back before publication.
- Launch from the success dialog, installed list, or GNOME application launcher. The launcher uses the executable's containing directory as its working directory, preserving adjacent bundled resources.
- The success dialog offers **Move Downloaded File to Trash**. A separate confirmation names the exact file. Its size and checksum are checked again. TidyLinux never automatically deletes it or falls back to permanent deletion.
- Use each row's menu for **Details** or **Uninstall**. Confirmation removes the managed bundle and its generated shortcut. Settings and user data outside that bundle remain intact. Changes stored inside the installation directory are removed with it.

## Local updates and rollback

Use an installed app's menu → **Update…**, select a local replacement archive or AppImage, review its launcher and optional version, then confirm. Close the app first. TidyLinux preserves its name, app ID, installation location, and desktop shortcut identity, and keeps one complete previous managed version. The menu also offers Launch, Details, and Uninstall.

Version labels are user supplied. Missing labels produce **Replace installation** with an explanation that ordering cannot be verified; filenames never establish that an archive is newer. Even with both labels present, TidyLinux does not compare versions or check online.

**Roll Back…** appears only for a validated retained version. It restores that version's files, metadata, launcher, icon, and shortcut; the displaced active version becomes the single rollback version. External settings are preserved rather than reverted. Uninstall confirmation covers both managed versions. Original downloads are never deleted automatically; the success dialog retains the explicit source-file Trash workflow.

Update preparation reuses the V1 snapshot/checksum and extraction engine. A private journal records staging, preparation, and commit. Startup inventory recovers interrupted uncommitted replacements to the original installation, or finishes cleanup after commit. Changed metadata, unsafe files, unrelated shortcuts, or an ambiguous journal stop recovery and produce a warning with the retained directory path. Mutations remain blocked while a recovery journal needs attention. See the release notes before handling those files manually.

Ctrl+Q closes the window. During an operation, closing requests cancellation; close again after the operation finishes. GTK follows your system's light/dark preference. There are no preferences that need configuration in V1.

## Supported archives and deliberate limits

Supported content formats are gzip tar (`.tar.gz`, `.tgz`), xz tar (`.tar.xz`), and ordinary ZIP. Detection uses content, not just extensions. Portable applications must already contain a usable Linux executable or launcher; TidyLinux does not supply missing libraries, runtimes, interpreters, or installation steps. It does not run `install.sh`, `setup.sh`, `uninstall.sh`, or `configure` as installer candidates.

Type 1 and type 2 AppImages are also supported, identified by their ELF/AppImage header rather than filename alone. They are copied intact into the managed payload as `application.AppImage`, made executable, and given the same desktop launcher and uninstall support. Inspection and installation never run or mount the AppImage. Header detection does not validate the embedded filesystem or guarantee compatibility. The original download is preserved. The installed copy and any saved logo count against the expanded-size limit below; only the logo is read from the embedded filesystem. Type 2 images with a SquashFS runtime prefix up to 16 MiB support embedded logos through the optional tool described above. Running an AppImage may require working FUSE support; see the [AppImage troubleshooting guide](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).

Safety policy:

- Absolute paths, traversal, control characters, backslashes, duplicate paths, and file/directory conflicts are rejected.
- **All symlinks and hard links are rejected**, including harmless internal ones. Sparse files, device nodes, FIFOs, encrypted/multipart ZIP, ZIP64, and unsupported tar member types are rejected. This intentionally excludes some common upstream application bundles; repackage them without links only if you understand the application. Do not disable validation to install one.
- Default bounds: 30,000 archive entries, 4 GiB compressed, 4 GiB expanded, 2 GiB per file, and a maximum declared 1000:1 expansion ratio. Hidden tar metadata is bounded to 1 MiB per record and a total decompressed-stream budget. ZIP central headers are checked before allocating the member inventory; directory metadata is limited to 64 MiB.
- Extraction uses exclusive new files in private staging, explicit validation, and Python's `tarfile.data_filter` when available. Permissions are normalized to private user read/write and executable permissions; setuid/setgid and other-user permissions are discarded. The selected launcher is executable, including when ZIP omitted Unix permissions.
- Scripts and ELF detection are heuristics, not authenticity checks. An ELF candidate can be a helper, have the wrong CPU architecture, or need unavailable libraries. Select the real application launcher. The optional version is entered by the user; V1 does not guess arbitrary version strings.
- Launch invokes the executable without a shell. Desktop entries quote paths and escape desktop field codes, including spaces and percent signs. For executable paths containing `%`, desktop entries use Fedora's `/usr/bin/env` to avoid GIO rejecting the path before field-code expansion; TidyLinux's own Launch action still invokes the executable directly. stdout/stderr of launched apps are discarded; immediate spawn failures are reported, but a process that starts and later exits is not monitored as an application-health check.

**Installed applications are not sandboxed.** They run with your permissions. Archive validation does not make downloaded software trustworthy, nor does this project claim complete security or compatibility. Install only trusted software. This is a user desktop utility, not a boundary against a hostile process already running as your account.

## Managed files and recovery

`$XDG_DATA_HOME` defaults to `~/.local/share`. Paths are:

```text
$XDG_DATA_HOME/tidylinux/apps/tidylinux-<UUID>/
    manifest.json
    payload/                 # full archive layout, not flattened
$XDG_DATA_HOME/tidylinux/staging/
$XDG_DATA_HOME/tidylinux/rollback/tidylinux-<UUID>/
    manifest.json
    payload/                 # one retained version
    desktop.backup
    rollback.json            # manifest/payload integrity receipt
$XDG_DATA_HOME/tidylinux/transactions/change-<UUID>/
    journal.json             # staging / prepared / committed
$XDG_DATA_HOME/tidylinux/lock
$XDG_DATA_HOME/applications/tidylinux-<UUID>.desktop
```

Manifests record identity, name/version, executable and icon, source path/size/checksum, timestamp, installed size, and the exact generated desktop path/checksum. A nonblocking file lock prevents competing mutations. Installation uses a private source snapshot, a staged complete manifest and payload, atomic directory publication and exclusive desktop publication. Handled errors and cancellation clean staging and roll back published files. UUID collisions never overwrite an installation.

Manifests must be regular files owned by the current user and are strictly validated when loaded and again before launch/removal. Inventory reads use the mutation lock. Broken records, missing directories, and leftover staging files appear as an inventory warning; unknown files are never automatically removed. A modified or symlinked desktop entry prevents uninstall until you restore the generated entry or explicitly remove that entry yourself. An already missing generated entry does not prevent uninstall. Existing manually installed apps, DNF/Flatpak apps and their shortcuts are never imported or modified.

A power failure or SIGKILL cannot run Python cleanup. Initial installation and uninstall retain V1's conservative behavior: private staging data or incomplete shortcut registration can remain and must be inspected with all instances closed. V1.1 update and rollback operations have a durable recovery journal; only validated, manager-owned transaction data is automatically cleaned. Unknown or damaged data is preserved and reported. Filesystems must support the required local atomic renames and `fsync`; hardware power-loss behavior has not been physically tested. Same-account concurrent tampering is outside the extraction threat model.

## TidyLinux's own desktop launcher

From the checkout, using the same interpreter you use to run the app:

```bash
python3 scripts/desktop_launcher.py install
```

This explicitly creates only `io.github.tidylinux.TidyLinux.desktop` under your XDG applications directory. It refuses to overwrite a different existing entry. The launcher refers to this checkout's absolute path; keep the checkout in place. The provided SVG is used as its icon. GNOME should discover the entry automatically.

Remove only this launcher:

```bash
python3 scripts/desktop_launcher.py uninstall
```

Use the same interpreter and checkout path for removal. A modified entry is preserved and reported for manual review. If installed into the virtual environment, remove the Python package with `.venv/bin/python -m pip uninstall tidylinux`, or remove your project virtual environment. The source checkout can then be removed normally. These steps preserve apps you installed through TidyLinux. To remove those apps, use **Uninstall** inside TidyLinux first; there is intentionally no broad recursive cleanup command targeting your home directory.

## Tests and checks

With Fedora's pytest installed:

```bash
PYTHONPATH=src python3 -m pytest -q
python3 -m compileall -q src scripts tests
python3 scripts/smoke_gui.py
```

Or use `.venv/bin/python -m pytest -q`. Core tests also run without pytest using `PYTHONPATH=src python3 -m unittest discover -s tests -v`.

All test fixtures use temporary directories, not the real application inventory or home directory. Tests cover formats, corrupt data, traversal/absolute paths, links and special files, declaration/metadata/expansion limits, candidate selection, manifest corruption, collisions, cancellation, injected installation failures, safe uninstall, desktop ownership, paths with spaces and shell metacharacters, launching, source identity checks and preservation on Trash failure. `desktop-file-validate` is used when available.

The graphical smoke script uses temporary XDG data/config/cache, starts a separate native window, and exercises empty/list views, review, recommendation/helper controls, installation, inventory, details, bundled icon rendering, the drop handler, fixture launch, uninstall, the native Trash action, About and file-dialog API construction. It checks a blocked worker's responsiveness and main-thread completion, light/dark mode, long paths, and review action visibility at 480 × 360. Test transitions are disabled for deterministic allocation. On mounts that do not support Trash, it checks error feedback and source preservation instead. It returns 77 if no display is accessible. Optional screenshots can be saved with `python3 scripts/smoke_gui.py --screenshots /tmp/tidylinux-verification`. It does not automate file-picker selection, pointer drag-and-drop, accessibility review, GNOME Shell search, or vendor-app compatibility.

## Architecture

- `archive.py`: content detection, bounded metadata/entry validation, executable/icon discovery, extraction.
- `installer.py`: staged installation, rollback, launch, ownership-checked uninstall, confirmed-source Trash helper.
- `registry.py`, `models.py`: XDG paths, lock, strict JSON records and inventory diagnostics.
- `desktop.py`: desktop-file encoding and generated-entry ownership checks.
- `launchers.py`: filename-based recommendations and helper identification shared by review and installation.
- `transactions.py`: durable replacement journal, integrity receipts, retained-version validation, and conservative recovery.
- `app.py`, `window.py`: application lifecycle and GTK/Libadwaita interface; blocking operations use a single worker, with GTK updates on the main thread.
- `scripts/`: checkout launch, explicit desktop integration, isolated GUI smoke test.
- `tests/`: isolated backend regression suite.

## Further work

Priorities are a reviewed internal-link policy to support more vendor bundles, extending journaled recovery to initial install/uninstall, architecture/dependency diagnostics, vendor-bundle integration tests, translations and accessibility review, and Fedora packaging. There is no app store, background updater, system cleaner, or root installation.

Licensed under GPL-3.0-or-later; see `LICENSE`.
