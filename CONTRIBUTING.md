# Contributing

Bug reports, documentation fixes, and focused patches are welcome. Read the [installation guide](docs/INSTALLATION.md) for Fedora dependencies and source launch. Use system PyGObject; avoid global pip changes.

`src/tidylinux` contains the GTK application and backend, `tests` contains offline fixtures, `data` contains desktop assets, `scripts` contains run/build/verification helpers, and `packaging/fedora` contains the RPM spec. Existing release notes record historical checks; they are not proof for a new patch.

```bash
python3 scripts/run.py
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/check_docs.py
git diff --check
python3 scripts/smoke_gui.py
bash scripts/build_rpm.sh
```

Use a disposable desktop account or VM for manual installs, updates, rollback, cleanup, and Trash. The smoke harness isolates XDG directories and mocks online requests. Never download/execute vendor applications in automated tests. See the [release checklist](docs/V1_4_RELEASE_CHECKLIST.md).

Follow existing straightforward Python style, use the standard library where practical, keep blocking work off the GTK thread, and test meaningful safety boundaries. Preserve explicit action gates, ownership checks, and rollback behavior. Avoid unrelated rewrites and dependencies. Tests must use temporary storage and mocked networking.

For bugs, include reproduction, expected/actual behavior, versions, and sanitized logs. For feature requests, describe the user problem and its scope. PRs should explain the behavior change and actual checks run; identify untested paths. Screenshot UI changes using real fixture data. Discuss larger changes before implementing them.

Follow [security reporting guidance](docs/SECURITY.md) for vulnerabilities. Do not publish private data or exploit details in ordinary issues. Contributions remain under the project's [GPL license](LICENSE).
