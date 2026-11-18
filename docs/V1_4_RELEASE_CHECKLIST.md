# V1.4 release preparation checklist

This is a development candidate, not a published release. Do not tag, push, publish, or install on the maintainer host automatically.

- [ ] Resolve all release-blocking failures and inspect the complete uncommitted diff.
- [ ] Ensure authoritative Python version, spec, AppStream, and candidate artifact agree.
- [ ] Run pytest, unittest, compilation, `git diff --check`, desktop/AppStream validation, documentation link checker, and workflow validation.
- [ ] Run native smoke with isolated XDG paths and mocked online requests; capture real screenshots.
- [ ] Build binary and source RPMs on clean Fedora; verify payload, requirements, scripts, metadata, SHA-256 and actual size.
- [ ] Test installed executable and launcher in a disposable graphical Fedora environment.
- [ ] Verify install, upgrade to higher package Release, and removal preserve fixture managed state and external settings. Do not use real home data.
- [ ] Test another supported Fedora release; record actual library/Python versions.
- [ ] Test GNOME Shell search, keyboard navigation, accessibility, compact windows and light/dark dialogs.
- [ ] Exercise installation, details, launch, local update, GitHub trust/source setup, metadata-only check, separate download/install approvals, rollback, Cleanup selection/cancellation/Trash, uninstall and recovery.
- [ ] Independently verify one trusted live GitHub source in disposable storage; automated tests remain offline.
- [ ] Review repository for credentials, private paths, unintended vendor binaries and generated artifacts. Preserve suspicious items for review.
- [ ] Check CI on GitHub (local YAML parsing does not prove hosted execution).
- [ ] Decide signing policy, verify final artifact checksums, and replace candidate documentation with actual tested support/artifact details.
- [ ] Maintainer approves final commit/tag and manual publication; upload matching RPM and checksum files only after approval.

## Current evidence

See [V1.4 development notes](V1_4_RELEASE_NOTES.md) for executed checks, including the Fedora 45 container lifecycle test. The unchecked list above remains the maintainer publication gate; do not infer cross-release or GNOME Shell validation from a virtual display. Existing V1.2/V1.3 notes describe historical checks, not a public V1.4 release. No automatic publication workflow is provided.
