# TidyLinux 1.0.0 — achieved scope

This is the historical V1 overview. For the subsequent update/rollback implementation, see the [V1.1 release notes](V1_1_RELEASE_NOTES.md).

Reviewed on November 17, 2026 against commit `9165e82` (`tidy linux v1`) and the current working tree. The package version is **1.0.0**.

TidyLinux now provides a native, user-level workflow for turning a downloaded portable Linux application into an installed app with a desktop shortcut, then launching or removing it later.

## What V1 delivers

| Area | Implemented behavior |
| --- | --- |
| Native desktop interface | GTK4/Libadwaita window, system light/dark appearance, file selection, local file drop handling, installed-app list, details, and confirmation dialogs. |
| Archive support | Content-based detection of `.tar.gz`/`.tgz`, `.tar.xz`, and ordinary ZIP archives. The original directory layout is preserved. |
| AppImage support | Type 1 and type 2 header detection; copies the image intact and makes the installed copy executable. Inspection and installation do not run or mount it. |
| Installation review | Discovers ELF executables and recognizable executable scripts; lets the user choose a launcher and edit the app name and optional version. |
| Desktop integration | Generates a per-app `.desktop` shortcut for the desktop application launcher, with path quoting and icon selection. |
| App artwork | Selects bundled archive icons; optionally reads a type 2 AppImage's `.DirIcon` through `unsquashfs`, with time and size limits and a generic-icon fallback. |
| Launching | Starts apps from TidyLinux or their desktop shortcut. Direct launching uses the executable's directory as its working directory to support adjacent resources. |
| Managed inventory | Stores manifests with app identity, source checksum, installation size, launcher, icon, timestamp, and generated-shortcut ownership information. Reports broken records. |
| Uninstall | Removes the managed bundle and its owned desktop shortcut after confirmation. Settings and user data outside the bundle remain. |
| Download cleanup | Offers a separately confirmed move of the original download to desktop Trash, after checking its size and checksum. No permanent-deletion fallback. |
| Background work | Runs blocking GUI operations through a worker thread and supports cancellation before installation publication. |
| Project setup | Python packaging and entry points, checkout launch scripts, an optional launcher for TidyLinux itself, an application icon, and GPL-3.0-or-later licensing. |

## Integrity and file handling

Installation uses a private copy of the reviewed download, verifies its SHA-256, re-inspects it, and extracts into private staging. The complete bundle is published atomically; handled failures roll back published installation files. A nonblocking lock prevents competing managed mutations.

Archive validation rejects traversal, absolute paths, duplicate paths, file/directory conflicts, all symlinks and hard links, and unsupported special files. Default limits include 30,000 entries, 4 GiB compressed and expanded sizes, 2 GiB per file, and a declared 1000:1 expansion ratio. Permissions are normalized, and application launching does not use a shell.

Manifests are validated before launch and removal. Modified or symlinked desktop entries block uninstall so that an unrelated or edited shortcut is preserved.

Apps install under `$XDG_DATA_HOME/tidylinux` (normally `~/.local/share/tidylinux`), with shortcuts under `$XDG_DATA_HOME/applications`. Installation needs no root access. The application has no runtime network dependency, telemetry, or background service.

## Additional work present in this checkout

The following changes are implemented locally but **not included in commit `9165e82`**:

- `src/tidylinux/launchers.py` recommends a launcher using filename and bundle-layout heuristics, including `AppRun` and VS Code's `code` launcher.
- The review UI labels recommended launchers and known helpers, explains its suggestion, and preselects a recommendation when available.
- The review UI disables installation when a launcher identified as a helper is selected. These checks use filenames; they do not execute bundle contents.

These changes remain local to the working tree. The follow-up reliability audit added direct recommendation tests, enforced helper rejection in the backend, and exercised the recommendation UI in the native GUI smoke test. See the [audit report](V1_RELIABILITY.md) for the complete changes and limitations.

## Validation evidence

The follow-up reliability review completed with **58 tests passed and 72 subtests passed** using `.venv/bin/python -m pytest -q`.

The suite covers archive formats and rejection rules, AppImage handling and artwork, installation and rollback, cancellation, source changes, manifest corruption, desktop ownership, launch behavior, uninstall, locking, and Trash failure preservation. Fixtures use temporary directories.

The isolated native GUI smoke test passed on GTK 4.24.1 and Libadwaita 1.10.0. Light/dark compact review screenshots were inspected. Trash on the test mount was unsupported, so error feedback and source preservation were verified. Passing these checks does not establish compatibility with arbitrary vendor applications or replace accessibility and full manual interaction review.

## Current boundaries and remaining work

- Portable bundles must already contain a usable launcher. TidyLinux does not supply missing libraries, interpreters, or installer steps.
- Symlinks and hard links are rejected, which excludes some upstream bundles. ZIP64 and encrypted or multipart ZIP are unsupported.
- Installed apps run with the user's permissions and are not sandboxed. Validation does not establish publisher trust or application compatibility.
- AppImages may require FUSE. Header recognition does not validate their complete embedded filesystem.
- Architecture/dependency diagnostics and monitoring of apps that start and then exit are not implemented.
- Power loss or forced termination can leave staging files or incomplete shortcut registration. Automatic crash recovery and cleanup remain future work.
- The README lists internal-link support, a durable recovery journal, vendor-bundle integration tests, translations, accessibility review, and Fedora packaging as V1.1 priorities.

See the [README](../README.md) for setup commands, detailed format limits, storage paths, recovery notes, and test instructions.
