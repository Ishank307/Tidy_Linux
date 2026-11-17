# TidyLinux V1.1 — safe local application updates

Implemented against the existing V1 checkout on November 17, 2026. This is a local implementation, not a published release. Existing local reliability changes and tests were preserved.

## Delivered behavior

- Installed-app menus now offer Update, Roll Back when available, Launch, Details, and Uninstall.
- Update selects a local TAR/ZIP archive or type 1/type 2 AppImage, reuses archive review and validation, and replaces the chosen managed app. Name, app ID, active installation location, and desktop shortcut path remain stable. No duplicate inventory entry is created.
- Review displays the current version, optional replacement version, archive path, launcher, and estimated installation size. Version labels come from the user. If either label is missing, the action is **Replace installation** and explains that version ordering cannot be verified. No filename-based or online version comparison is performed.
- One complete prior managed version is retained, including its payload, manifest, icon references, and exact desktop entry. Successful subsequent updates discard only the verified older retained version.
- Rollback verifies the retained manifest, executable, saved desktop entry, payload contents, relative paths, permissions, and ownership. It restores the previous metadata and files and retains the displaced active version, allowing the user to reverse the rollback without accumulating history.
- External settings and user data are untouched. Managed files created inside the installation are part of its version snapshot. Rollback does not reverse external configuration migrations made by an app.
- Original archives remain untouched. Success dialogs offer the existing separately confirmed, checksum-checked source Trash action. Uninstall confirmation includes the retained rollback version.

Close the application being replaced before updating or rolling back. Running processes are not detected or stopped automatically. A process that retains open files can otherwise change a retained payload and make its integrity check fail.

## Transaction and recovery model

The existing process-wide nonblocking file lock serializes install, update, rollback, launch checks, inventory recovery, and uninstall. No service, network check, new runtime dependency, or periodic polling was added. Blocking work and payload integrity checks run in the existing GUI worker.

Private managed directories under `$XDG_DATA_HOME/tidylinux` (normally `~/.local/share/tidylinux`) are:

```text
apps/<app-id>/                 active payload and V1-compatible manifest
rollback/<app-id>/             one retained complete bundle
transactions/change-<uuid>/    journal and replacement work
staging/                      existing V1 install/uninstall work
```

The active bundle receives manager metadata `desktop.backup` and `rollback.json` when it is prepared for retention. The receipt hashes its manifest and payload; it is an accidental-corruption/integrity check, not publisher authentication or a defense against a malicious process with the same user permissions.

| Durable journal state | Meaning | Startup recovery |
| --- | --- | --- |
| `staging` | The original bundle is active. The candidate may be incomplete in private work storage. | Keep the validated original; remove only the transaction's owned work. |
| `prepared` | Candidate and original are complete and durable; renames or desktop publication may have begun. | Restore the validated original and its desktop entry; restore the prior rollback version if there was one. |
| `committed` | Replacement, shortcut, and one retained original have been published and synced. | Keep the replacement and retained original; finish validated cleanup. |

Files and directory entries are synced before activation, after renames, and at journal publication. Desktop replacement uses a synced temporary file in the desktop directory and an atomic rename. Rollback prepares a private copy so its source remains available until commit. Cancellation is honored during update preparation; once activation begins, completion/recovery takes precedence over cancellation.

If ordinary failure occurs before commit, the engine attempts the same undo path immediately. If recovery itself fails, the error identifies the transaction directory, files remain available, and startup retries or reports the problem. A committed update is not reported as failed merely because staging cleanup must be retried.

Recovery refuses unknown journal fields/paths, nonregular metadata, wrong ownership, changed payloads, unsafe links, and unrelated desktop entries. It reports the retained location in the inventory banner and blocks further managed mutations while an unresolved transaction remains. It never chooses between unverifiable versions or silently removes the last known original. Recovery can itself be interrupted and retried.

Initial install/uninstall still use V1 staging and conservative leftover reporting; they have not been converted to this replacement journal.

## Verification

Results after implementation:

- Pytest: **91 tests passed, 142 subtests passed** using the existing virtual environment. All original 58 tests and their 72 subtests remain.
- System-Python unittest discovery: **91 tests passed**.
- `compileall` and `git diff --check`: passed.
- `desktop-file-validate`: used by the existing and new tests; passed. The existing TidyLinux launcher still produces an informational System/Utility category hint.
- Native smoke: passed on **GTK 4.24.1 / Libadwaita 1.10.0**, including review-based replacement, unknown-version wording, known-version Update wording, stable identity/shortcut, launch, Roll Back menu availability, confirmed rollback, and uninstall with a retained version. Existing V1 GUI checks also pass.
- Compact light/dark and update-review screenshots were inspected at 480 × 360. Actions stay visible and review contents scroll. No-display smoke returns 77.

New backend coverage includes all supported archive families and both AppImage headers, explicit unknown-version replacement, stable identity and shortcut, preserved downloads/settings/unrelated files, changed source rejection, invalid launcher rejection, cancellation, one-version retention, user rollback and reverse rollback, corrupt retained metadata/payload, concurrency and stale records, uninstall after update, failed shortcut generation/publication, partial shortcut writes, disk-full staging and rollback copying, and deferred cleanup retry.

Crash checks interrupt every update `fsync` boundary, activation renames with and without a previous backup, journal transitions, and recovery renames. A separate Python process exits with `os._exit` after moving the original, then a fresh installer recovers it. Corrupt journals and unrelated desktop edits remain untouched and generate warnings.

These are isolated fixtures and fault injections. **No physical power cut, kernel/filesystem crash, or real publisher-app update was tested.** Persistence depends on the filesystem/storage honoring local atomic rename and `fsync`; unusual mounts can fail safely and require manual recovery. Native Trash success was unavailable on this system's `/tmp` mount; the real error dialog and download preservation were verified instead. GUI AppImages are synthetic headers and are not executed. Pointer-driven file-picker selection, GNOME Shell search, and accessibility still require manual checks.

## Fedora launch and checks

From the repository root:

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita python3-pytest desktop-file-utils squashfs-tools
python3 scripts/run.py
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/smoke_gui.py
python3 scripts/smoke_gui.py --screenshots /tmp/tidylinux-v1-1
```

`squashfs-tools` remains optional for AppImage artwork. Here system Python lacks pytest; `.venv/bin/python -m pytest -q` was used. GUI smoke runs a separate application instance against disposable XDG directories and fixture apps.

## Manual user testing

Use a disposable Fedora desktop account or VM for real publisher bundles and GNOME Shell discovery. For fixture-only testing, follow the isolated inventory setup in [the V1 reliability checklist](V1_RELIABILITY.md#manual-interaction-checklist-with-an-isolated-inventory). Close any normal TidyLinux instance first so its single-instance activation cannot intercept the isolated launch.

1. Install a trusted disposable bundle with a version label. Record its details, app ID, shortcut filename, and an external settings file.
2. Open its menu → Update. Use the native picker to select another bundle. Check the current label, optional new label, selected path/launcher, and size. Cancel once and confirm nothing changed.
3. Leave the new version empty and verify Replace installation wording. Enter a version and confirm the label changes to Update only when the current version is also known; do not treat this as a compatibility or ordering guarantee.
4. Confirm replacement. Verify one row, the same shortcut path, working launch, retained original downloads, and unchanged external settings. Close/reopen TidyLinux and verify inventory persistence.
5. Choose Roll Back, cancel once, then confirm. Verify the original launcher/icon/version and desktop launch. Roll back again to verify the displaced version is retained.
6. Update a second time and verify only the immediate predecessor remains. Uninstall and confirm both managed versions and the shortcut disappear, while downloads and unrelated data remain.
7. Inspect the review at a small window size, both system color schemes, and using keyboard-only navigation. Exercise native picker cancellation, a real file drag, and GNOME search in the disposable desktop account.

For any recovery warning, close all TidyLinux instances and the affected app. Preserve the entire named transaction directory and both `apps/<id>` and `rollback/<id>` bundles. Inspect the journal, manifests, receipts, and shortcut before explicitly approving any manual restoration. There is intentionally no generic recursive cleanup or force-recovery command: unknown/corrupt data requires a case-specific decision, and should not be deleted to make the warning disappear. Low-disk-space recovery may need space freed outside these retained bundles before reopening TidyLinux.

Rollback storage adds one installed payload; preparation also needs the replacement snapshot/payload, and user rollback temporarily copies its target. Integrity checks read retained payloads and can take time for large bundles. Installed trees containing application-created links, special files, unexpected root metadata, or data exceeding retention bounds are refused rather than backed up unsafely. The existing archive limits and all-link rejection remain unchanged. DNF, Flatpak, and external installations are never imported or updated.
