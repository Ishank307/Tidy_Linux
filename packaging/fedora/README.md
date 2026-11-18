# Fedora RPM build

Test host: Fedora 45. Other Fedora releases and architectures remain unverified. Packaging follows Fedora's [Python macros](https://fedoraproject.org/wiki/Changes/DeprecateSetuppyMacros): wheel build/install, system runtimes, no bundled interpreter. The package is noarch but Python dependencies are resolved for the build distribution.

Install build tools (this installs tools, not TidyLinux):

```bash
sudo dnf install rpm-build python3-devel pyproject-rpm-macros python3-setuptools python3-wheel python3-pytest desktop-file-utils appstream
bash scripts/build_rpm.sh
```

The script creates a unique directory under `dist/rpmbuild.*`, generates an allowlisted source archive including uncommitted source changes, builds binary/source RPMs, and verifies metadata, dependencies, files, absence of lifecycle scripts, and SHA-256. It never installs the application. Missing prerequisites fail explicitly. Outputs are retained for inspection, not removed with a broad cleanup command.

```bash
find dist -name '*.rpm' -type f
bash scripts/verify_release.sh /absolute/path/to/the/actual/tidylinux.rpm
```

The verifier writes the matching `.sha256` beside each binary RPM and verifies it. Publish that checksum file with exactly that RPM. Packages are unsigned development artifacts; a checksum is not publisher authentication.

`source_archive.py` uses stable ordering, normalized ownership/modes/timestamps, and gzip time zero. It excludes Git, virtual environments, caches and build outputs by allowlist. Repeated source generation from identical inputs should match. This does not promise bit-identical RPMs across different toolchains or Fedora releases. Use a clean Fedora VM/container or Mock for independent release builds. No developer paths are embedded deliberately.

The spec owns only system Python, executable, desktop, icon, AppStream, license, and documentation files. No lifecycle scripts invoke the app or touch home directories. For stronger verification, use an isolated Fedora VM: create fixture managed user state, install the RPM, launch it, update to a higher Release build, remove the RPM, and compare that state before/after. Never install the test RPM on the developer host without approval. Real GNOME Shell search needs desktop testing.

CI runs Fedora 45 tests, a virtual-display smoke check, RPM build and artifact verification. Workflow artifacts are not GitHub Releases. Extend to another supported Fedora version only after validating dependencies and behavior there.

## Isolated lifecycle test

With rootless Podman available and two built RPM versions (older, then candidate):

```bash
bash scripts/test_rpm_container.sh /absolute/path/to/older.rpm /absolute/path/to/candidate.rpm
```

These are explicit path placeholders, not published downloads. The script uses the official Fedora 45 image and read-only source/artifact mounts. DNF resolves dependencies inside the disposable container. A non-root fixture user installs a tiny local test bundle and retains a rollback version/source metadata before RPM installation. Content snapshots check preservation after install, upgrade and removal. Installed GUI code runs under Xvfb with startup networking/scanning mocked to fail. The host RPM database and real home are never used. This is virtual-display launch testing, not GNOME Shell search testing.
