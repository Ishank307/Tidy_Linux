# Security

Only install applications from publishers you trust. TidyLinux runs programs with normal user privileges and provides no sandbox. A valid archive or GitHub URL does not establish authenticity or compatibility. Never run the manager as root.

Archive inspection/extraction rejects traversal, symlinks, hard links, special files, sparse files, and excessive sizes/expansion. Managed manifests and desktop files are validated before mutation. Private manager directories, ownership checks, and a nonblocking lock protect cooperating operations. These checks are not isolation from malicious code already running as your user.

Online checks happen only after explicit action. HTTPS uses certificate verification; configured GitHub API endpoints and approved asset CDN redirects are constrained. No tokens, cookies, environment proxy credentials, or downloaded scripts are used. Downloads enforce size budgets, verify available API SHA-256 digests, and pass through archive validation. No independent signature verification is provided. Signed CDN query strings are not persisted in application metadata or included in application error messages.

Transaction journals support recovery and retain a complete previous installation. Verification failures do not authorize deleting the active bundle. Ambiguous state remains for investigation. External application settings are not managed or removed.

Cleanup requires explicit scan and selection/confirmation; manually reviewed folders need another confirmation. Changed candidates and missing retained duplicates are skipped. Native Trash may fail, and trashed data can still consume space. There is no permanent deletion fallback. Gio Trash is path-based: concurrent hostile rename/substitution races cannot be eliminated by preflight checks. Close other tools modifying the same files. Root or malicious same-user processes are outside the trust boundary.

RPM upgrades/removal own only system payload and do not run TidyLinux operations in scriptlets. Checksums verify bytes relative to the checksum file, not publisher identity. Development RPMs are unsigned; maintainers must decide release signing and distribution policy before publication.

## Reporting vulnerabilities

No private security reporting address has been established. Use GitHub's private vulnerability reporting feature **if the repository offers it**, otherwise open an issue requesting a private channel without exploit details or sensitive files. Do not assume ordinary issues are confidential. Remove credentials, personal paths, and private archives from reports.
