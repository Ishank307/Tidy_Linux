# Smart Cleanup

Open the main menu → **Cleanup**. Opening the dialog does not scan. Downloads is an optional initial location. Choose another location if desired and press **Scan**. Scans run on a worker and can be cancelled. Default scanning is nonrecursive, limited to 10,000 direct entries, and only considers `.tar.gz`, `.tgz`, `.tar.xz`, `.zip`, `.rpm`, `.deb`, and `.AppImage` files. It does not inspect RPM/DNF or Flatpak installations.

Results show the path, size, classification/reason, and TidyLinux ownership. Nothing is preselected. Sizes describe reviewed files, not guaranteed reclaimable space.

- **Exact duplicate download:** equal size and full SHA-256 content. Same names alone do not count. The named retained copy is excluded from selection; its hash is rechecked before each other copy is trashed. Different bytes, including different versions, are not duplicates.
- **Previously tracked installation archive:** source path, size, and checksum match a current or rollback manifest. Its necessity remains unverified. It may still be useful as your original installer.
- **Known TidyLinux temporary leftover:** a validated private download marker and contents, with no pending transaction. Active bundles and rollback directories are excluded. Ambiguous staging files and unknown downloads are reported and preserved.
- **Manually selected folder for review:** explicitly inspect a folder to see total size and entry count. It remains unverified and needs a separate additional confirmation. This is not automatic abandoned-installation detection. Do not select an application or personal folder you still need.

Folder inspection is bounded to 12 levels and 10,000 entries, rejects symlinks and filesystem crossings, and hashes in chunks. It may take time for large data. Managed storage and ancestors/overlaps of managed installations cannot be manually selected. No heuristic based on names or archive resemblance marks a folder as junk.

Select candidates and choose **Move Selected to Trash**. Review the paths and confirm. Current identity, metadata, duplicate hashes, ownership markers, pending journals, and the operation lock are checked again. Changed files are skipped. Trash errors preserve the file; there is no permanent-delete fallback. Files in Trash may still occupy space until you empty Trash yourself.

Gio's path-based Trash cannot make revalidation and rename atomic against hostile external filesystem changes. Avoid concurrent modifications. TidyLinux operations share a nonblocking lock, but arbitrary other processes do not obey it. See [security](SECURITY.md).
