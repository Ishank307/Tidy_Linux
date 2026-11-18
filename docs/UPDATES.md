# Updates and rollback

For a local update, choose an application's **Update…** menu action, select a replacement archive, review the executable/version, and approve replacement. Identity and the verified desktop shortcut are preserved. A failed transactional update preserves or recovers the previous installation.

For online checks, open **Update Source…**, enter `https://github.com/OWNER/REPOSITORY`, and separately confirm that you trust it. Linking is local-only. TidyLinux cannot establish that a repository is official. Only public GitHub Releases sources are supported; credentials and private repositories are not supported.

Click **Check for Updates** to fetch latest stable release metadata. There is no startup network access, automatic retry, polling, schedule, or automatic asset download. Drafts and prereleases are rejected. Statuses distinguish Not configured, Checking, Unable to check, Version unknown, Update available, and Up to date. Up to date requires a valid numeric comparison. Unknown version labels report that comparison is unavailable; they are not ordered heuristically.

**Review release…** shows assets filtered by supported format and filename architecture hints. Filenames do not prove binary compatibility. Select the exact asset yourself, check its publisher/runtime requirements and checksum information, then explicitly choose **Download for review**. Download is not installation. The next review needs a separate **Update / Replace installation** approval. Cancel either review to stop.

HTTPS API/CDN restrictions, size limits, available GitHub SHA-256 digests, and archive protections apply. No checksum sidecars or signatures are fetched automatically. A checksum from the same provider is not independent publisher authentication. Network errors and rate limits appear per application; retry only by clicking Check again.

Rollback retains one complete previous bundle and does not depend on keeping the source archive. Source configuration persists across rollback. Interrupted transactions are examined by existing recovery on inventory load without network requests. Ambiguous state is preserved and reported. Do not manually delete journal or rollback directories to silence warnings. See [troubleshooting](TROUBLESHOOTING.md) and [security](SECURITY.md).
