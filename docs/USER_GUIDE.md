# User guide

Choose **Install App** or drop a supported portable archive/AppImage onto the window. Inspection runs without executing the archive. Review the detected executables, choose the actual application rather than a helper, enter a name and optional version, and confirm. An executable label is not proof of safety. Obtain bundles from publishers you trust.

Application rows offer Launch and a menu with Details, Update, Update Source, Roll Back when available, and Uninstall. Details includes paths and update status. Installed bundles and GNOME shortcuts keep a stable managed identity across updates. Close an application before replacement, rollback, or uninstall; TidyLinux does not terminate its processes.

**Update…** accepts a local replacement archive. It presents review and explicit replacement confirmation. **Roll Back** restores the retained version and swaps the current version into the rollback slot. Only one previous version is retained. See [updates](UPDATES.md) for online sources and recovery.

Uninstall removes the owned bundle, retained rollback version, and verified shortcut. External settings and your original archives remain. The separate source-archive Trash action checks the historical checksum before moving an original archive. No source file is automatically deleted after installation.

Manager-owned online downloads are private temporary files. Successful online installation cleans them when safe; interrupted operations may retain them for recovery. **Cleanup** offers conservative review of eligible leftovers and downloads. See [cleanup](CLEANUP.md). It does not promise that any file is unnecessary.

Keyboard shortcuts: Ctrl+O opens an archive, Ctrl+R checks updates, Ctrl+Shift+C opens Cleanup, Ctrl+W or Ctrl+Q closes the window. Work is cancellable where supported; a blocking system call can delay cancellation.
