# Troubleshooting

- **Unsupported archive:** use a supported portable format. Links, traversal, special files, sparse files, excessive expansion, and malformed archives are deliberately rejected. Do not disable validation to install an incompatible bundle.
- **No executable / helper selected:** inspect the publisher's bundle instructions. Choose the application binary, not a crash handler, sandbox helper, or installer script. Some bundles need system installation and cannot be managed here.
- **Missing shared library:** launch the trusted app from a terminal to read its error. Install dependencies from Fedora using the publisher's guidance; TidyLinux does not bundle a runtime or resolve arbitrary libraries.
- **AppImage / FUSE:** some images require host FUSE support. Inspect trusted publisher instructions for your Fedora release. `squashfs-tools` is optional for artwork inspection, not a guarantee the image will run.
- **Failed launch:** inspect Details for the executable path and confirm its dependencies and architecture. Run `tidylinux` from a terminal to see errors. Never run it with sudo.
- **Missing/broken shortcut:** check Details and the desktop environment's refresh behavior. A differing shortcut is preserved rather than overwritten. A source-checkout launcher may shadow the RPM launcher; remove it with the matching checkout helper only if it still matches. Real GNOME Shell search needs manual desktop validation.
- **Failed update / recovery:** keep the journal, retained version, and downloaded archive intact. Close other TidyLinux instances and reopen. If recovery reports ambiguous data, preserve a copy before investigating; never recursively delete manager state to hide the error.
- **GitHub error / timeout / rate limit:** verify the linked public repository independently, check connectivity, wait if rate-limited, then explicitly press Check again. Renamed repositories or changed CDN behavior can be rejected by endpoint restrictions. Proxy-only networks may not work; credentials are not used.
- **Trash unavailable:** the file is preserved and an error is shown. Some mounts, including this test environment's `/tmp`, do not support native Trash. Move files using your file manager after reviewing them; TidyLinux never falls back to deletion.
- **Another operation is in progress:** wait or close another instance normally. Do not delete lock files or journals to force concurrent operations.

When reporting a bug, include Fedora, Python/GTK/Libadwaita versions, reproduction steps, and sanitized error text. Do not upload private archives, credentials, signed download URLs, or personal application data. See [contributing](../CONTRIBUTING.md).
