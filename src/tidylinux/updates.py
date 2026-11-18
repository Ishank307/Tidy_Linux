"""Explicit-action-only GitHub releases client. Construction performs no I/O.

No caller in startup, inventory, launch, or recovery invokes this client's HTTP
methods. Check, download, and installation have separate affirmative gates.
Only standard-library networking is used; there is no token or cookie store.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin
from urllib.request import (Request, build_opener, ProxyHandler, HTTPSHandler,
                            HTTPRedirectHandler)
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import stat
import time
import uuid

from .archive import Limits, check_cancel, inspect_archive
from .models import AppRecord, Cancelled, Inspection, TidyError
from .registry import ID_PATTERN, Paths
from .sources import SourceStore, repository_url
from .transactions import atomic_write, read_json, require_recovered, sync

Progress = Callable[[str, float], None]
METADATA_LIMIT = 2 * 1024 * 1024
API_VERSION = "2026-03-10"


def numeric_version(value: str) -> tuple[int, ...] | None:
    if not isinstance(value, str) or len(value) > 100:
        return None
    if not re.fullmatch(r"v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}", value):
        return None
    numbers = tuple(map(int, value.removeprefix("v").split(".")))
    return numbers + (0,) * (4 - len(numbers))


def compatibility(name: str, machine: str) -> str | None:
    """None excludes an asset; a label describes only filename indications."""
    lower = name.lower()
    if not lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".zip", ".appimage")):
        return None
    tokens = set(re.findall(r"[a-z0-9]+", lower))
    if tokens & {"windows", "win", "win32", "win64", "darwin", "macos", "osx", "mac"}:
        return None
    aliases = {"x86_64": {"x86_64", "amd64", "x64"}, "aarch64": {"aarch64", "arm64"},
               "i686": {"i386", "i686", "x86"}, "armv7l": {"armv7", "armv7l", "armhf"}}
    host = next((arch for arch, words in aliases.items() if machine.lower() in words or machine.lower() == arch), machine.lower())
    # Treat x86_64 as a single token rather than the incompatible 32-bit x86.
    arch_tokens = set(re.findall(r"[a-z0-9]+", lower.replace("x86_64", "amd64")))
    indicated = {arch for arch, words in aliases.items() if arch_tokens & words}
    if indicated and host not in indicated:
        return None
    linux = "linux" in tokens or lower.endswith(".appimage")
    if linux and indicated == {host}:
        return f"Linux / {host} indicated; compatibility is not verified."
    return "Compatibility unknown. Verify Linux, architecture, and runtime with the publisher before selecting."


@dataclass(frozen=True)
class ReleaseAsset:
    asset_id: int
    name: str
    size: int
    sha256: str | None
    compatibility: str


@dataclass(frozen=True)
class Release:
    repository: str
    tag: str
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class CheckResult:
    record: AppRecord
    status: str
    detail: str
    release: Release | None = None


@dataclass(frozen=True)
class Downloaded:
    app_id: str
    release: Release
    asset: ReleaseAsset
    directory: Path
    inspection: Inspection


def release_from_metadata(repository: str, data: dict, machine: str) -> Release:
    if (not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False
            or not isinstance(data.get("tag_name"), str) or not data["tag_name"]
            or len(data["tag_name"]) > 100
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in data["tag_name"])):
        raise TidyError("No valid stable release metadata was returned.")
    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) > 1000:
        raise TidyError("The release asset list is invalid or too large.")
    choices = []
    seen = set()
    names = set()
    for item in assets:
        if not isinstance(item, dict) or item.get("state") != "uploaded":
            continue
        name, size, asset_id = item.get("name"), item.get("size"), item.get("id")
        if (not isinstance(name, str) or len(name) > 255 or name in {"", ".", "..", "download.json"}
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF or c in "/\\" for c in name)
                or len(name.encode("utf-8")) > 255 or name in names
                or type(size) is not int or not 0 < size <= Limits().archive_bytes
                or type(asset_id) is not int or asset_id <= 0 or asset_id in seen):
            raise TidyError("Release contains invalid or oversized asset metadata.")
        seen.add(asset_id)
        names.add(name)
        match = compatibility(name, machine)
        if match is None:
            continue
        checksum = item.get("digest")
        if checksum is not None:
            if not isinstance(checksum, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
                raise TidyError("Release asset has an unsupported or malformed checksum.")
            checksum = checksum[7:]
        choices.append(ReleaseAsset(asset_id, name, size, checksum, match))
    return Release(repository_url(repository), data["tag_name"], tuple(choices))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class GitHubClient:
    def __init__(self, *, opener=None, machine: str | None = None, limits: Limits = Limits()):
        self.opener = opener  # Lazily construct it only inside an explicit action.
        self.machine = machine or platform.machine()
        self.limits = limits

    def _open(self, endpoint: str, *, binary: bool, cancel: Event | None):
        if not re.fullmatch(r"https://api\.github\.com/repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/releases/" +
                            (r"assets/[1-9][0-9]*" if binary else "latest"), endpoint):
            raise TidyError("Only the configured GitHub Releases API endpoints are allowed.")
        url = endpoint
        opener = self.opener or build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
        for _ in range(6):
            check_cancel(cancel)
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                    or parts.port not in (None, 443) or parts.fragment or "\\" in url
                    or any(ord(c) < 33 or ord(c) == 127 for c in url)):
                raise TidyError("Unsafe GitHub request or redirect was blocked.")
            cdn = (binary and parts.hostname in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                   and parts.path.startswith("/github-production-release-asset"))
            if url != endpoint and not cdn:
                raise TidyError("Redirect outside the approved GitHub release destinations was blocked.")
            headers = {"User-Agent": "TidyLinux", "Accept-Encoding": "identity",
                       "Accept": "application/octet-stream" if binary else "application/vnd.github+json"}
            if parts.hostname == "api.github.com":
                headers["X-GitHub-Api-Version"] = API_VERSION
            try:
                response = opener.open(Request(url, headers=headers), timeout=10)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    exc.close()
                    if not location:
                        raise TidyError("GitHub returned a redirect without a destination.") from None
                    url = urljoin(url, location)
                    continue
                code = exc.code
                exc.close()
                raise TidyError(f"GitHub request failed (HTTP {code}). The public source may be unavailable or rate limited.") from None
            if response.getcode() != 200 or response.geturl() != url:
                response.close()
                raise TidyError("Unexpected GitHub response or unchecked redirect was blocked.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                response.close()
                raise TidyError("Unexpected encoded response was blocked.")
            return response
        raise TidyError("GitHub exceeded the redirect limit.")

    def check(self, record: AppRecord, repository: str, *, requested: bool = False,
              cancel: Event | None = None) -> CheckResult:
        if not requested:
            raise TidyError("Checking releases requires an explicit user action.")
        repository = repository_url(repository)
        repo = repository.removeprefix("https://github.com/")
        try:
            started = time.monotonic()
            with self._open(f"https://api.github.com/repos/{repo}/releases/latest", binary=False, cancel=cancel) as response:
                content = bytearray()
                while True:
                    check_cancel(cancel)
                    if time.monotonic() - started > 30:
                        raise TidyError("The release check timed out.")
                    chunk = response.read1(min(65536, METADATA_LIMIT + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > METADATA_LIMIT:
                        raise TidyError("GitHub release metadata exceeds the size limit.")
            release = release_from_metadata(repository, json.loads(content), self.machine)
            check_cancel(cancel)
            old, new = numeric_version(record.version), numeric_version(release.tag)
            if old is None or new is None:
                return CheckResult(record, "Version unknown", "Version comparison unavailable. Review the publisher's release before replacing.", release)
            status = "Update available" if new > old else "Up to date"
            detail = f"Installed: {record.version}. GitHub latest stable label: {release.tag}. Compatibility is not guaranteed."
            if not release.assets:
                detail += " No supported asset matches this machine; use a verified local archive."
            return CheckResult(record, status, detail, release)
        except Cancelled:
            raise
        except (OSError, URLError, ValueError, RecursionError, TidyError) as exc:
            check_cancel(cancel)
            detail = str(exc) if isinstance(exc, TidyError) else "Network timeout, connection failure, or invalid release metadata. Try again manually."
            return CheckResult(record, "Unable to check", detail)

    def download(self, paths: Paths, record: AppRecord, release: Release, asset: ReleaseAsset,
                 *, confirmed: bool = False, cancel: Event | None = None,
                 progress: Progress | None = None) -> Downloaded:
        if not confirmed:
            raise TidyError("Downloading requires explicit confirmation after asset review.")
        if asset not in release.assets or not 0 < asset.size <= self.limits.archive_bytes:
            raise TidyError("Select an approved asset within the download size limit.")
        # Revalidate even programmatically supplied metadata before any request.
        validated = release_from_metadata(release.repository, {"draft": False, "prerelease": False,
            "tag_name": release.tag, "assets": [{"id": asset.asset_id, "name": asset.name,
            "size": asset.size, "state": "uploaded", "digest": "sha256:" + asset.sha256 if asset.sha256 else None}]}, self.machine)
        if validated.assets != (asset,):
            raise TidyError("Asset metadata or compatibility changed. Check again manually.")
        with paths.locked():
            require_recovered(paths)
            if SourceStore(paths).read(record) != release.repository:
                raise TidyError("The configured source changed. Check again before downloading.")
            directory = paths.downloads / ("download-" + uuid.uuid4().hex)
            directory.mkdir(mode=0o700)
            atomic_write(directory / "download.json", json.dumps({"schema": 1, "app_id": record.app_id,
                "repository": release.repository, "asset_id": asset.asset_id, "name": asset.name,
                "size": asset.size, "sha256": asset.sha256}).encode())
            sync(paths.downloads)
            sync(paths.root)
        target = directory / asset.name
        try:
            repo = release.repository.removeprefix("https://github.com/")
            checksum, copied = hashlib.sha256(), 0
            started = time.monotonic()
            with self._open(f"https://api.github.com/repos/{repo}/releases/assets/{asset.asset_id}", binary=True, cancel=cancel) as response:
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isascii() or not length.isdigit() or int(length) != asset.size):
                    raise TidyError("Asset size changed since review. Check again manually.")
                with target.open("xb") as output:
                    while True:
                        check_cancel(cancel)
                        if time.monotonic() - started > 1800:
                            raise TidyError("The download timed out.")
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > min(asset.size, self.limits.archive_bytes):
                            raise TidyError("The download exceeds its reviewed size or safety limit.")
                        output.write(chunk)
                        checksum.update(chunk)
                        if progress:
                            progress(f"Downloading {asset.name}: {copied} / {asset.size} bytes", copied / asset.size)
                    output.flush()
                    os.fsync(output.fileno())
            if copied != asset.size or (asset.sha256 and checksum.hexdigest() != asset.sha256):
                raise TidyError("Downloaded asset size or SHA-256 does not match the reviewed metadata.")
            if progress:
                progress("Inspecting downloaded archive…", 1)
            inspection = inspect_archive(target, self.limits, cancel)
            if inspection.checksum != checksum.hexdigest():
                raise TidyError("Downloaded asset changed during inspection.")
            check_cancel(cancel)
            sync(directory)
            return Downloaded(record.app_id, release, asset, directory, inspection)
        except Exception as exc:
            discard_download(paths, directory)
            check_cancel(cancel)
            if isinstance(exc, (Cancelled, TidyError)):
                raise
            raise TidyError("Download failed or timed out; the installed application was preserved.") from None


def check_installed(records: list[AppRecord], sources: SourceStore, client: GitHubClient,
                    *, requested: bool = False, cancel: Event | None = None,
                    progress: Progress | None = None) -> list[CheckResult]:
    if not requested:
        raise TidyError("Checking for updates requires an explicit user action.")
    results = []
    for index, record in enumerate(records):
        check_cancel(cancel)
        if progress:
            progress(f"Checking {record.name} ({index + 1}/{len(records)})", index / max(len(records), 1))
        try:
            source = sources.read(record)
            result = (client.check(record, source, requested=True, cancel=cancel) if source else
                      CheckResult(record, "Not configured", "Link a repository using Update Source in the app menu."))
        except (OSError, ValueError, TidyError) as exc:
            if isinstance(exc, Cancelled):
                raise
            result = CheckResult(record, "Unable to check", str(exc))
        results.append(result)
    check_cancel(cancel)
    return results


def validate_download(paths: Paths, directory: Path) -> dict:
    paths.prepare()
    info = directory.lstat()
    if (directory.parent != paths.downloads or not re.fullmatch(r"download-[0-9a-f]{32}", directory.name)
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise TidyError("Unrecognized download directory was preserved.")
    data = read_json(directory / "download.json")
    name = data.get("name")
    if (set(data) != {"schema", "app_id", "repository", "asset_id", "name", "size", "sha256"}
            or type(data["schema"]) is not int or data["schema"] != 1
            or not isinstance(data["app_id"], str) or not ID_PATTERN.fullmatch(data["app_id"])
            or repository_url(data["repository"]) != data["repository"]
            or type(data["asset_id"]) is not int or data["asset_id"] <= 0
            or type(data["size"]) is not int or not 0 < data["size"] <= Limits().archive_bytes
            or (data["sha256"] is not None and (not isinstance(data["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"])))
            or not isinstance(name, str) or name in {"", ".", "..", "download.json"}
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF or c in "/\\" for c in name)
            or len(name.encode("utf-8")) > 255
            or {p.name for p in directory.iterdir()} - {"download.json", name}):
        raise TidyError("Unrecognized download contents were preserved.")
    for path in directory.iterdir():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise TidyError("Unsafe download contents were preserved.")
    return data


def discard_download(paths: Paths, directory: Path) -> None:
    validate_download(paths, directory)
    shutil.rmtree(directory)
    sync(paths.downloads)


def install_download(installer, record: AppRecord, downloaded: Downloaded, executable: str,
                     version: str, *, approved: bool = False, cancel: Event | None = None) -> AppRecord:
    if not approved or downloaded.app_id != record.app_id:
        raise TidyError("Installing the downloaded replacement requires explicit approval.")
    data = validate_download(installer.paths, downloaded.directory)
    if (data != {"schema": 1, "app_id": record.app_id, "repository": downloaded.release.repository,
                 "asset_id": downloaded.asset.asset_id, "name": downloaded.asset.name,
                 "size": downloaded.asset.size, "sha256": downloaded.asset.sha256}
            or downloaded.inspection.source != downloaded.directory / downloaded.asset.name
            or downloaded.inspection.archive_size != downloaded.asset.size
            or (downloaded.asset.sha256 and downloaded.inspection.checksum != downloaded.asset.sha256)):
        raise TidyError("The approved download metadata changed; the current installation was preserved.")
    if SourceStore(installer.paths).read(record) != downloaded.release.repository:
        raise TidyError("The linked source changed. Review the source again.")
    updated = installer.update(record, downloaded.inspection, executable, version, cancel)
    # Never discard files while a transaction still requires recovery. Failure
    # to clean a committed download is diagnostic, not a failed installation.
    if not any(installer.paths.transactions.iterdir()):
        try:
            discard_download(installer.paths, downloaded.directory)
        except (OSError, ValueError, TidyError):
            pass
    return updated
