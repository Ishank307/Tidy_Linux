# V1 reliability audit and user testing

Date: November 17, 2026. Base commit: `9165e82`. Version remains **1.0.0**. Existing local launcher and documentation work was retained. No release was created and no existing developer application was installed, launched, or uninstalled by these tests.

## Findings and fixes

| Priority | Finding | Change and regression coverage |
| --- | --- | --- |
| High | A manifest replaced by a FIFO could block inventory indefinitely. File ownership was not checked. | Reject nonregular and foreign-owned manifests before reading. Tests cover FIFO and simulated foreign ownership. Existing symlink/corruption tests remain. |
| High | GIO rejected a desktop entry whose executable path contained `%`, despite successful syntax validation. | Use `/usr/bin/env` for that desktop command so field-code expansion occurs on an argument. No shell is involved. A real GIO launch test verifies spaces, `%`, quotes, dollar signs, backticks, and backslashes. The shared formatter also handles TidyLinux's interpreter path. |
| Medium | Helper rejection existed only in the review UI. | Enforce the same rule before backend installation writes anything; add direct helper and recommendation tests. |
| Medium | Leftover staging data was invisible, and inventory could race a mutation. | Inventory holds the existing nonblocking lock and reports retained staging files without deleting them. Add preservation and warning coverage. |
| Medium | ELF discovery discarded every name containing `.so`, including `demo.something`. | Restrict that exclusion to `.so` and `.so.` library patterns; test executable discovery. |
| Medium | Long launcher paths could widen the review; action buttons scrolled out of view. | Ellipsize launcher labels with full-path tooltips, wrap long informational text, and keep Cancel/Install in a bottom bar. Native checks cover long paths and button bounds at 480 × 360. |
| Low | Closing during a noncancellable operation claimed cancellation. | Report that the user must wait; native test verifies that no cancellation event is set. |
| Low | The no-display smoke check could continue with no GDK display. | Check the actual default display before constructing the application. |

Manifest name/version length validation now matches installation bounds. Installed-list titles and subtitles have line limits to keep rows compact.

The desktop issue was reproduced locally. GIO's [desktop loader implementation](https://github.com/GNOME/glib/blob/main/gio/gdesktopappinfo.c) checks the parsed command executable before launch-time field expansion, which explains why a doubled-percent executable path was rejected.

## Automated verification

- Pytest through the existing `.venv/bin/python`: **58 tests passed, 72 subtests passed**. System Python has no pytest installed in this environment; the system-Python pytest invocation failed for that reason.
- Standard-library unittest discovery through system Python: **58 tests passed**.
- Compilation: `python3 -m compileall -q src scripts tests` passed, as did `git diff --check`.
- Native smoke: passed on **GTK 4.24.1 / Libadwaita 1.10.0** in the Fedora graphical session.
- With `DISPLAY` and `WAYLAND_DISPLAY` unset and the X11 backend selected, the smoke script printed its no-display skip and returned **77**.
- `desktop-file-validate` is available and used by the backend tests. It emits an informational hint about the existing TidyLinux launcher having both System and Utility categories; this is not a validation error.

Disposable portable bundles exercise gzip tar, xz tar, and ZIP layouts containing a main launcher, an alternate launcher, a Chromium-style helper, a resource directory, and a shared library. Tests inspect candidates, check recommendations, choose the alternate executable, install, launch against adjacent resources, reload inventory in a **fresh Python process**, uninstall, and preserve the source and an unrelated shortcut. Existing tests also execute a copy of the host's native `true` binary.

Additional coverage includes failed manifest writes, existing extraction/publication rollback cases, cancellation, desktop modification/symlink protection, and deliberately retained staging files. An internal `AppRun -> bin/demo` link is still rejected; security policy was not loosened for vendor layouts.

Native checks exercise the drop **handler**, installation through the review's Install button, helper selection, success/details/confirmation dialogs, toasts, inventory refresh, launch and uninstall, AppImage copy/remove, About, and file-dialog API construction. A deliberately blocked worker allows five main-loop timer ticks, and its completion callback is checked against the main thread. The test disables transitions to avoid sampling animated widget allocations.

Light and dark screenshots, including a long launcher path, were inspected at 480 × 360. Review buttons remain visible and launcher text is bounded. Screenshots can be regenerated in `/tmp`; they are not repository assets.

Native Trash **success was not verified**: this system's `/tmp` mount refuses Trash. The real error dialog and preservation of the source were verified instead. The fixture AppImage used in the GUI is a synthetic header and was not executed; backend AppImage tests cover detection, copying, and bounded artwork extraction.

## Remaining limitations

- No downloaded vendor application was tested. These are realistic, disposable fixtures, not a compatibility certification for VS Code, Electron, or other publishers.
- Pointer-driven file selection and drag-and-drop, keyboard/focus behavior, screen-reader accessibility, and GNOME Shell search visibility require manual testing. The test constructs file-dialog APIs and invokes the drop handler; it does not perform those interactions.
- Launcher recommendations and helper classification are filename heuristics. They cannot prove the right entry point, architecture, dependencies, or trustworthiness, and may exclude an unusually named legitimate launcher.
- All internal links remain unsupported. Some real bundles therefore cannot be installed unchanged. Dependencies, FUSE, and vendor runtimes are not provided.
- Staging warnings are diagnostic only. Forced termination, power loss, or failed removal can retain payload data. There is no crash journal or automatic recovery, and orphan desktop entries are not reconciled automatically. Never delete retained files until you have inspected them with all instances closed.
- Existing shortcuts are not rewritten by this change. A previously installed app with a percent-containing executable path retains its old shortcut until it is explicitly reinstalled. Its in-app Launch action remains usable.
- Apps run with the user's permissions. Manifest checks are not protection against a hostile process running under the same account.

## Fedora commands

From the repository root, install missing system dependencies and launch:

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita python3-pytest desktop-file-utils squashfs-tools
python3 scripts/run.py
```

`squashfs-tools` is optional for type 2 AppImage artwork. GTK bindings come from Fedora; no global pip install is needed.

Run all checks from the checkout:

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/smoke_gui.py
python3 scripts/smoke_gui.py --screenshots /tmp/tidylinux-verification
```

The GUI checks need a graphical session and return 77 when none is accessible. Both backend and GUI tests use temporary application inventories.
If using the existing project environment instead of Fedora's pytest package, run `.venv/bin/python -m pytest -q`.

## Manual interaction checklist with an isolated inventory

First close any running TidyLinux window: the normal application uses a single-instance ID, so an already running process could receive the activation instead of the isolated instance. Then run this in a terminal from the repository root:

```bash
test_root=$(mktemp -d /tmp/tidylinux-manual-XXXXXX)
export XDG_DATA_HOME="$test_root/data"
export XDG_CONFIG_HOME="$test_root/config"
export XDG_CACHE_HOME="$test_root/cache"
export GSETTINGS_BACKEND=memory
python3 - <<'PY'
import io, os, tarfile
from pathlib import Path
root = Path(os.environ['XDG_DATA_HOME']).parent
with tarfile.open(root / 'Demo-linux-x64.tar.gz', 'w:gz') as archive:
    for name, data, mode in [
        ('Demo-linux-x64/demo', b'#!/bin/sh\ncat resources/message.txt > launched.txt\n', 0o755),
        ('Demo-linux-x64/alternate.sh', b'#!/bin/sh\nprintf alternate > launched.txt\n', 0o755),
        ('Demo-linux-x64/chrome-sandbox', b'#!/bin/sh\nexit 0\n', 0o755),
        ('Demo-linux-x64/resources/message.txt', b'fixture launched', 0o644),
    ]:
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode = len(data), mode
        archive.addfile(entry, io.BytesIO(data))
print(root / 'Demo-linux-x64.tar.gz')
PY
python3 scripts/run.py
```

1. Use Ctrl+O and select the printed archive path in the native picker. Cancel the review, then drag that archive from Files into the window.
2. Confirm `demo` is recommended; choose `chrome-sandbox` and verify Install is disabled. Choose `alternate.sh`, edit the name/version, and install.
3. Check the success dialog, installed row, Details, and Launch. Confirm a `launched.txt` file containing `alternate` appears beside the installed launcher under `$XDG_DATA_HOME/tidylinux/apps`.
4. Close and reopen with `python3 scripts/run.py` in the same terminal. Verify the record persists. Test uninstall cancellation, then confirm uninstall and check that the source archive remains.
5. Repeat with the main launcher. Resize to 480 × 360, scroll the review, and inspect long text, focus order, keyboard controls, tooltips, and system light/dark appearance. Verify both action buttons stay accessible.
6. Test the confirmed Trash action with a disposable download on a mount supporting Trash. On an unsupported mount, confirm error feedback and that the download remains.

Run GNOME Shell search and actual publisher AppImage/FUSE testing in a disposable Fedora user account or VM. An isolated `XDG_DATA_HOME` inside the app does not change the running GNOME Shell's inventory. After testing, close the app and leave this terminal to restore your normal XDG environment; inspect the temporary tree before removing it.
