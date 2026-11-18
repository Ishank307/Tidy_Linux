# TidyLinux V1.2 — user-initiated release checking

Implemented from TidyLinux 1.1.0, commit `095acc9`. No release was published and no commits were created or pushed. The existing portable-app installer, archive checks, desktop identity, transaction engine, and one-version rollback are reused.

The source version was changed to **1.2.0** only after implementation and verification passed.

## User-controlled workflow

The existing window has one compact **Check for Updates** action. App menus include **Update Source…**, alongside local Update, Roll Back, Launch, Details, and Uninstall. Sources are optional; unlinked apps continue to work offline.

There are three distinct affirmative actions:

| User action | Authorized behavior | What follows |
| --- | --- | --- |
| Check for Updates | Fetch release metadata from the repositories already linked to installed apps. | Update the existing app-list statuses. No asset download or installation. |
| Select an asset and confirm Download for review | Fetch exactly that asset into private manager storage; verify and inspect it. | Open installation review. No installation. |
| Confirm Update / Replace installation | Apply the reviewed payload with V1.1's transaction engine. | Keep the previous version for rollback and clean the owned temporary download when safe. |

Opening TidyLinux, reloading inventory, linking/removing a source, opening a review, launching an app, and recovery do **not** check for updates. No startup checks, schedules, retry loops, update polling, automatic downloads, automatic installations, or update daemon were added. UI progress uses worker callbacks delivered to GTK on the main thread; it does not schedule network activity.

The source editor accepts a canonical public `https://github.com/OWNER/REPOSITORY` URL. A separate trust confirmation is required before saving. TidyLinux does not verify that the repository is official. Linking/removing a source itself is local-only.

## Results and conservative comparison

Existing list rows display **Checking**, **Update available**, **Up to date**, **Not configured**, **Unable to check**, or **Version unknown**. Status appears before size/version text to remain visible in compact windows. Tooltip and Details show the full explanation and any source/network error. Available or incomparable releases with supported assets offer **Review release…**.

The client requests GitHub's documented latest stable release endpoint and independently rejects draft/prerelease flags. Comparison accepts an optional lowercase `v` and two to four nonnegative numeric components, without leading zeros; missing trailing components are zero. Labels such as `nightly`, `release-2.0`, `2.0rc1`, or build-suffixed versions produce **Version unknown** and **Version comparison unavailable**. Up to date is shown only after a valid numeric comparison against that configured repository's latest stable label. It is not a claim that every release or installed executable has been inspected for compatibility.

Release assets are filtered to supported portable formats and filename indications of Linux/host architecture. Known Windows/macOS or other-architecture names are excluded. Generic or ambiguous names are marked **Compatibility unknown**. **Every asset requires explicit selection**, even if only one is offered; there is no silent substitution or preselected recommendation. Architecture, libraries, FUSE, and publisher compatibility remain the user's responsibility.

## Network boundaries and verification

- Standard-library HTTPS with normal certificate verification; no new runtime dependencies.
- Initial requests are restricted to the configured repository's `api.github.com/repos/OWNER/REPO/releases/latest` or `releases/assets/ID` path. User-provided download URLs and `browser_download_url` values are not followed.
- Metadata redirects cannot silently change repositories. Binary redirects are checked individually, with a limit, and allowed only to HTTPS `release-assets.githubusercontent.com` or `objects.githubusercontent.com` release-asset paths. Repository renames or future CDN changes require a fresh user decision or client change, rather than a broader redirect fallback.
- No authentication tokens, Authorization headers, cookie store, browser credentials, netrc credentials, or environment proxy credentials. Only public repositories are supported. Corporate proxy-only networks may not work. API/CDN signed redirect queries are not stored in application metadata or included in application error messages.
- Socket operations use a 10-second timeout. Checks enforce a 30-second metadata budget at read boundaries; downloads enforce a 30-minute budget at read boundaries. Reads use `read1` to avoid waiting for full buffers from a trickling server. Cancellation is checked between requests/reads, and underlying blocking network/DNS work can delay cancellation. There are no automatic retries.
- Metadata is limited to 2 MiB and 1,000 asset entries. Asset/download sizes are bounded by the existing archive limit, and received size must equal the reviewed API size. Redirects and encoded responses cannot bypass these bounds.
- GitHub's `sha256:` asset digest is verified when supplied. Unsupported/malformed supplied digests are rejected; absence is explicitly displayed, and size/archive checks still apply. Separate checksum/signature assets are not fetched automatically. A GitHub digest checks consistency with the selected release metadata; it is not independent publisher authentication.
- Downloaded content is inspected without execution using V1's content detection and extraction policy. The source snapshot/checksum is checked again by the transaction engine. Existing path, link, ownership, expansion, helper, manifest, and desktop ownership checks remain.

Implementation follows GitHub's [latest release documentation](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) and [release asset download documentation](https://docs.github.com/en/rest/releases/assets#get-a-release-asset), using API version `2026-03-10`.

## Managed files, cleanup, and recovery

Source configuration lives in `$XDG_DATA_HOME/tidylinux/sources/<app-id>.json`, with schema, managed app ID, and confirmed canonical repository. It is separate from the V1 manifest and version snapshots, so existing records need no schema migration and rollback does not revert a user's source choice. Only owned, regular, bounded metadata is accepted. Uninstall removes the linked metadata along with the owned installation.

Downloads live in private `$XDG_DATA_HOME/tidylinux/downloads/download-<uuid>/` directories. A manager marker records the app ID, repository, selected asset ID/name, reviewed size, and optional checksum. The filename, ownership, directory namespace, marker, and contents are validated before cleanup or installation. Unknown files and links are preserved rather than recursively removed.

Cancelling a download or its final review cleans only its verified temporary directory. A successful installation cleans that download unless a transaction remains unresolved. A cleanup error does not turn a committed installation into a reported failure. Failed installation, abrupt exit, or interrupted recovery can retain the download, which startup reports without requests, resume, or installation. Inspect retained files with all TidyLinux instances closed; there is no broad cleanup command.

The installed manifest retains its historical source path/size/checksum even when that manager download is removed. Rollback uses the complete retained bundle, not the original archive, so it continues to work after cleanup. Manager cache files do not receive the user-download Trash button. User-selected original archives are never changed by this workflow.

## Verified results

- **132 pytest tests passed, 218 subtests passed** using `.venv/bin/python -m pytest -q`.
- **132 tests passed** with system-Python unittest discovery.
- All existing V1.1 tests remain and pass, including transaction/recovery failure injection and desktop validation.
- `compileall` and `git diff --check` passed.
- Native smoke passed on **GTK 4.24.1 / Libadwaita 1.10.0**, using temporary XDG directories and mocked HTTP responses. Any attempt to construct a real update network opener is rejected by the smoke test.
- GUI checks cover invalid source input, separate source confirmation, a fresh window with a configured source making no requests, manual metadata-only checking, missing/unknown/current/available/error statuses, unselected assets, both approvals, download-only preservation of the active version, cancellation cleanup, stable identity/shortcut, online rollback, and uninstall.
- Light/dark asset review and the app list were inspected at **480 × 360**; action buttons stay visible, content scrolls, and labels are bounded. Existing responsiveness/main-thread checks also pass. No-display smoke exits **77**.

New backend tests mock network exceptions, metadata, redirect chains, bodies, timeouts, missing sources, version labels, invalid asset metadata, architecture/platform names, checksum/size failures, cancellation, explicit-action gates, startup/recovery with no network, source persistence through rollback, cleanup safety, failed installation, and retained downloads after an interrupted transaction. No third-party program was downloaded or installed.

## Limitations and manual verification

Live GitHub API/CDN connectivity and a real publisher's release have **not** been exercised end to end. Tests mock HTTP responses and verify the client boundaries. Native picker pointer interaction, actual drag-and-drop, accessibility, GNOME Shell visibility, physical power-loss durability, and vendor runtime compatibility still need manual testing. Trash success remains unavailable on this environment's `/tmp` mount; its failure dialog and original-source preservation were verified.

There is no support for private repositories, prerelease selection, non-GitHub providers, automatic checks, checksum sidecar fetching, signature verification, or download resumption. Results are in memory and become unknown again after restarting. GitHub rate limits/errors remain per-app statuses until another explicit check. Corrupt or interrupted source metadata may need manual inspection; the application will not overwrite it to hide the error.

From the repository root on Fedora:

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita python3-pytest desktop-file-utils squashfs-tools
python3 scripts/run.py
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/smoke_gui.py
python3 scripts/smoke_gui.py --screenshots /tmp/tidylinux-v1-2
```

Here system Python lacks pytest, so the existing `.venv/bin/python -m pytest -q` was used. `squashfs-tools` is optional for type 2 AppImage artwork; no new dependency is required for update checks.

Use a disposable Fedora desktop account/VM for live release testing, or the isolated inventory setup in [the V1 reliability checklist](V1_RELIABILITY.md#manual-interaction-checklist-with-an-isolated-inventory). Close any existing normal TidyLinux instance before launching with temporary XDG variables, to avoid its single-instance activation intercepting the isolated launch.

1. Install a trusted disposable bundle and record its ID, shortcut, external settings, and original archive. Open/reopen the app without pressing Check for Updates; verify no release requests occur using your network monitor.
2. Open Update Source, try an invalid URL, enter a publisher repository you have independently verified, and cancel the trust confirmation. Confirm no source is saved and no request occurs. Repeat and explicitly confirm the link.
3. Click Check for Updates. Check its progress/cancellation and each app's status/Details. An unknown installed/tag label must not say Up to date or Update available. Retry an offline or rate-limited source only by clicking Check again.
4. Open Review release, inspect asset alternatives and compatibility/checksum explanations, then cancel. Confirm no asset was downloaded. Reopen, choose the exact trusted Linux asset, and confirm Download for review.
5. Cancel the final installation review once; verify the managed temporary download is removed and the installation/shortcut remains unchanged. Repeat the download and explicitly approve installation. Verify one inventory row, the same ID/shortcut, working launch, retained external settings/original archives, and a cleaned manager cache.
6. Roll back and verify the previous launcher/icon/version. Reopen TidyLinux and confirm no check or download resumes. Remove the source or uninstall in the disposable inventory; confirm unrelated files and original archives remain.
7. Inspect both reviews with small windows, both color schemes, keyboard-only navigation, and a screen reader. Source/download/installation confirmations should remain distinct and accessible.
