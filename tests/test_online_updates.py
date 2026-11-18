"""All HTTP responses are mocked; only disposable portable apps are installed."""
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest import TestCase, mock
from urllib.error import HTTPError, URLError
import hashlib
import io
import json
import os
import socket
import tarfile
import tempfile

from tidylinux.archive import Limits, inspect_archive
from tidylinux.installer import Installer
from tidylinux.models import Cancelled, TidyError
from tidylinux.registry import Paths, Registry
from tidylinux.sources import SourceStore, repository_url
from tidylinux.updates import (GitHubClient, NoRedirect, check_installed, compatibility, discard_download,
                              install_download, numeric_version, release_from_metadata)

REPOSITORY = "https://github.com/Publisher/Demo"
LATEST = "https://api.github.com/repos/Publisher/Demo/releases/latest"
ASSET = "https://api.github.com/repos/Publisher/Demo/releases/assets/17"
CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1/file?signature=private"


class Response(io.BytesIO):
    def __init__(self, content, url, headers=None):
        super().__init__(content)
        self.url, self.headers = url, headers or {}

    def getcode(self):
        return 200

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, dict(request.header_items()), timeout))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(request.full_url)
        content, headers = value
        return Response(content, request.full_url, headers)


def bundle_bytes(version):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        content = f"#!/bin/sh\nprintf '{version}' > result\n".encode()
        entry = tarfile.TarInfo(f"Demo-{version}/run")
        entry.size, entry.mode = len(content), 0o755
        archive.addfile(entry, io.BytesIO(content))
    return output.getvalue()


def metadata(content, tag="v2.0", **changes):
    value = {"draft": False, "prerelease": False, "tag_name": tag, "assets": [
        {"id": 17, "name": "demo-linux-x86_64.tar.gz", "size": len(content),
         "state": "uploaded", "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
         "browser_download_url": "https://attacker.invalid/ignored"}]}
    value.update(changes)
    return value


class OnlineUpdateTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tidylinux-online-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = Paths(self.root / "data")
        self.installer = Installer(self.paths)
        self.original = self.root / "original.tar.gz"
        self.original.write_bytes(bundle_bytes("1"))
        self.record = self.installer.install(inspect_archive(self.original), "Demo-1/run", "Demo", "1.0")
        self.sources = SourceStore(self.paths)
        self.content = bundle_bytes("2")
        self.data = metadata(self.content)
        self.release = release_from_metadata(REPOSITORY, self.data, "x86_64")
        self.asset = self.release.assets[0]

    def link(self):
        self.sources.configure(self.record, REPOSITORY, confirmed=True)

    def client(self, responses):
        opener = Opener(responses)
        return GitHubClient(opener=opener, machine="x86_64"), opener

    def download(self, client=None, **kwargs):
        self.link()
        if client is None:
            client, _ = self.client([(self.content, {"Content-Length": str(len(self.content))})])
        return client.download(self.paths, self.record, self.release, self.asset, confirmed=True, **kwargs)

    def test_valid_and_invalid_repository_urls(self):
        for value in (REPOSITORY, REPOSITORY + "/", "https://github.com/a-b/example.repo"):
            with self.subTest(value=value):
                self.assertEqual(repository_url(value), value.rstrip("/"))
        for value in ("http://github.com/a/b", "https://github.com.evil/a/b", "https://user:secret@github.com/a/b",
                      "https://github.com/a/b?token=secret", "https://github.com/a/b#fragment",
                      "https://github.com/a/b.git", "https://github.com/a/..", "https://github.com/a/b/releases",
                      "https://github.com/a%2fb/c", "https://github.com:443/a/b", " https://github.com/a/b",
                      "https://github.com/a/b\n", "https://github.com/-bad/b", "file:///tmp/release", None):
            with self.subTest(value=value), self.assertRaises(TidyError):
                repository_url(value)

    def test_conservative_versions(self):
        self.assertEqual(numeric_version("v1.2"), numeric_version("1.2.0"))
        self.assertGreater(numeric_version("2.10"), numeric_version("2.9.9"))
        for value in ("", "release-2.0", "2", "2.0rc1", "2.0-beta", "01.2", "2.0+build", "1.2.3.4.5", " 2.0", None):
            with self.subTest(value=value):
                self.assertIsNone(numeric_version(value))

    def test_architecture_and_supported_formats(self):
        for name in ("demo-linux-x86_64.tar.gz", "demo-amd64.AppImage", "demo-linux-x64.tgz", "demo-linux-x64.zip", "demo-linux-x64.tar.xz"):
            with self.subTest(name=name):
                self.assertIsNotNone(compatibility(name, "x86_64"))
        for name in ("demo-linux-arm64.tar.gz", "demo-linux-i686.AppImage", "demo-windows-x64.zip", "demo-darwin-x64.zip",
                     "demo-linux-x64.deb", "demo.tar.bz2", "demo.exe"):
            with self.subTest(name=name):
                self.assertIsNone(compatibility(name, "x86_64"))
        self.assertIn("unknown", compatibility("demo.zip", "x86_64"))
        self.assertIn("unknown", compatibility("demo-x64.zip", "x86_64"))
        self.assertIn("aarch64", compatibility("demo-linux-arm64.tar.gz", "aarch64"))

    def test_all_network_and_installation_gates_are_explicit(self):
        client, opener = self.client([])
        for operation in (lambda: client.check(self.record, REPOSITORY),
                          lambda: check_installed([self.record], self.sources, client),
                          lambda: client.download(self.paths, self.record, self.release, self.asset)):
            with self.assertRaisesRegex(TidyError, "explicit"):
                operation()
        with self.assertRaisesRegex(TidyError, "explicit"):
            self.sources.configure(self.record, REPOSITORY)
        downloaded = self.download()
        with mock.patch.object(self.installer, "update") as update:
            with self.assertRaisesRegex(TidyError, "explicit"):
                install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0")
            update.assert_not_called()
        self.assertEqual(opener.calls, [])

    def test_no_network_at_construction_inventory_link_launch_or_recovery(self):
        with mock.patch("tidylinux.updates.build_opener", side_effect=AssertionError("unexpected network")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("unexpected socket")):
            GitHubClient()
            self.link()
            self.assertEqual(Registry(self.paths).inventory()[0], [self.record])
            self.assertEqual(self.installer.launch(self.record).wait(timeout=5), 0)
            self.sources.configure(self.record, None, confirmed=True)

    def test_source_persists_across_local_update_rollback_and_is_removed_on_uninstall(self):
        self.link()
        replacement = self.root / "local.tar.gz"
        replacement.write_bytes(self.content)
        updated = self.installer.update(self.record, inspect_archive(replacement), "Demo-2/run", "2.0")
        self.assertEqual(SourceStore(self.paths).read(updated), REPOSITORY)
        restored = self.installer.rollback(updated)
        self.assertEqual(SourceStore(self.paths).read(restored), REPOSITORY)
        self.installer.uninstall(restored)
        self.assertEqual(list(self.paths.sources.iterdir()), [])
        self.assertTrue(self.original.exists() and replacement.exists())

    def test_source_ownership_symlinks_corruption_and_stale_records(self):
        self.link()
        path = self.sources.path(self.record)
        external = self.root / "external.json"
        path.rename(external)
        path.symlink_to(external)
        with self.assertRaises(TidyError):
            self.sources.configure(self.record, None, confirmed=True)
        self.assertTrue(external.is_file())
        path.unlink()
        path.write_text('{"schema": 1, "app_id": "other", "repository": "https://github.com/a/b"}')
        records, warnings = self.installer.registry.inventory()
        self.assertEqual(records, [self.record])
        self.assertTrue(warnings)
        with self.assertRaises(TidyError):
            self.sources.configure(self.record, REPOSITORY, confirmed=True)
        with self.assertRaises(TidyError):
            self.sources.read(replace(self.record, version="stale"))

    def test_missing_sources_make_no_requests(self):
        client, opener = self.client([])
        results = check_installed([self.record], self.sources, client, requested=True)
        self.assertEqual(results[0].status, "Not configured")
        self.assertEqual(opener.calls, [])

    def test_check_fetches_metadata_only_does_not_download_or_install(self):
        self.link()
        client, opener = self.client([(json.dumps(self.data).encode(), {})])
        with mock.patch.object(client, "download", side_effect=AssertionError("automatic download")), \
                mock.patch.object(self.installer, "update", side_effect=AssertionError("automatic install")):
            result = check_installed([self.record], self.sources, client, requested=True)[0]
        self.assertEqual(result.status, "Update available")
        self.assertEqual([call[0] for call in opener.calls], [LATEST])
        self.assertEqual(list(self.paths.downloads.iterdir()), [])
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])

    def test_statuses_require_valid_version_comparison(self):
        for installed, tag, expected in (("1.0", "v2.0", "Update available"), ("2.0", "v2.0", "Up to date"),
                                         ("3.0", "v2.0", "Up to date"), ("", "v2.0", "Version unknown"),
                                         ("1.0", "release-2", "Version unknown"), ("nightly", "v2.0", "Version unknown")):
            with self.subTest(installed=installed, tag=tag):
                client, _ = self.client([(json.dumps(metadata(self.content, tag)).encode(), {})])
                result = client.check(replace(self.record, version=installed), REPOSITORY, requested=True)
                self.assertEqual(result.status, expected)
                if expected == "Version unknown":
                    self.assertIn("comparison unavailable", result.detail)

    def test_stable_release_filtering(self):
        for flags in ({"draft": True}, {"prerelease": True}, {"draft": None}, {"prerelease": 0}):
            with self.subTest(flags=flags):
                client, _ = self.client([(json.dumps(metadata(self.content, **flags)).encode(), {})])
                result = client.check(self.record, REPOSITORY, requested=True)
                self.assertEqual(result.status, "Unable to check")
                self.assertIsNone(result.release)

    def test_no_supported_asset_does_not_claim_binary_compatibility(self):
        data = metadata(self.content)
        data["assets"][0]["name"] = "demo-windows-x64.zip"
        client, _ = self.client([(json.dumps(data).encode(), {})])
        result = client.check(self.record, REPOSITORY, requested=True)
        self.assertEqual(result.release.assets, ())
        self.assertIn("No supported asset", result.detail)

    def test_network_timeout_http_and_malformed_metadata(self):
        values = [TimeoutError("private URL must not leak"), URLError("private token"),
                  HTTPError(LATEST, 403, "secret", {}, None), (b"not JSON", {}),
                  (b"x" * (2 * 1024 * 1024 + 1), {})]
        for value in values:
            with self.subTest(value=type(value)):
                client, _ = self.client([value])
                result = client.check(self.record, REPOSITORY, requested=True)
                self.assertEqual(result.status, "Unable to check")
                self.assertNotIn("private", result.detail)
                self.assertNotIn("secret", result.detail)

    def test_checks_can_be_cancelled_without_an_http_request(self):
        client, opener = self.client([])
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            client.check(self.record, REPOSITORY, requested=True, cancel=cancel)
        self.assertEqual(opener.calls, [])

    def test_metadata_redirects_cannot_switch_repositories(self):
        client, opener = self.client([HTTPError(LATEST, 302, "redirect", {"Location": "https://api.github.com/repos/Other/Demo/releases/latest"}, None)])
        result = client.check(self.record, REPOSITORY, requested=True)
        self.assertEqual(result.status, "Unable to check")
        self.assertEqual(len(opener.calls), 1)

    def test_only_approved_https_download_redirects_and_no_credentials(self):
        client, opener = self.client([HTTPError(ASSET, 302, "redirect", {"Location": CDN}, None), (self.content, {})])
        downloaded = self.download(client)
        self.assertEqual([call[0] for call in opener.calls], [ASSET, CDN])
        for _, headers, timeout in opener.calls:
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("Cookie", headers)
            self.assertEqual(timeout, 10)
        self.assertNotIn("X-github-api-version", opener.calls[1][1])
        self.assertTrue(downloaded.inspection.source.is_file())

    def test_unsafe_download_redirects_are_blocked_before_contact(self):
        for location in ("http://release-assets.githubusercontent.com/github-production-release-asset/file",
                         "https://attacker.invalid/file", "https://api.github.com/repos/Other/Demo/releases/assets/17",
                         "https://user:password@release-assets.githubusercontent.com/github-production-release-asset/file",
                         "https://release-assets.githubusercontent.com:444/github-production-release-asset/file",
                         "https://release-assets.githubusercontent.com/unapproved/file", "https://127.0.0.1/file",
                         CDN + "#fragment"):
            with self.subTest(location=location):
                client, opener = self.client([HTTPError(ASSET, 302, "redirect", {"Location": location}, None)])
                with self.assertRaises(TidyError):
                    self.download(client)
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(list(self.paths.downloads.iterdir()), [])

    def test_download_size_limits_truncation_and_changed_checksum(self):
        cases = [(self.content + b"extra", {}), (self.content[:-1], {}),
                 (self.content, {"Content-Length": "1"}), (b"x" * len(self.content), {})]
        for content, headers in cases:
            with self.subTest(size=len(content)):
                client, _ = self.client([(content, headers)])
                with self.assertRaises(TidyError):
                    self.download(client)
                self.assertEqual(list(self.paths.downloads.iterdir()), [])
        self.link()
        client = GitHubClient(opener=Opener([]), machine="x86_64", limits=Limits(archive_bytes=1))
        with self.assertRaises(TidyError):
            client.download(self.paths, self.record, self.release, self.asset, confirmed=True)

    def test_download_cancellation_and_progress(self):
        cancel, progress = Event(), []
        def report(text, fraction):
            progress.append((text, fraction))
            cancel.set()
        with self.assertRaises(Cancelled):
            self.download(cancel=cancel, progress=report)
        self.assertTrue(progress)
        self.assertEqual(list(self.paths.downloads.iterdir()), [])
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])

    def test_invalid_archive_is_never_installed(self):
        invalid = b"not an archive"
        self.release = release_from_metadata(REPOSITORY, metadata(invalid), "x86_64")
        self.asset = self.release.assets[0]
        client, _ = self.client([(invalid, {})])
        with self.assertRaises(TidyError):
            self.download(client)
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])

    def test_download_alone_never_installs_then_approved_install_preserves_identity_and_rollback(self):
        downloaded = self.download()
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])
        self.assertTrue(downloaded.directory.exists())
        updated = install_download(self.installer, self.record, downloaded, "Demo-2/run", "v2.0", approved=True)
        self.assertEqual(updated.app_id, self.record.app_id)
        self.assertEqual(updated.desktop_path, self.record.desktop_path)
        self.assertFalse(downloaded.directory.exists())
        self.assertEqual(self.sources.read(updated), REPOSITORY)
        self.assertEqual(self.installer.rollback(updated), self.record)
        self.assertTrue(self.original.exists())

    def test_changed_download_fails_install_without_losing_current(self):
        downloaded = self.download()
        downloaded.inspection.source.write_bytes(self.content + b"modified")
        with self.assertRaises(TidyError):
            install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0", approved=True)
        self.assertEqual(self.installer.registry.load(self.paths.apps / self.record.app_id), self.record)
        self.assertTrue(downloaded.directory.exists())
        self.assertTrue(self.original.exists())

    def test_failed_transactional_update_restores_original_and_keeps_download(self):
        downloaded = self.download()
        with mock.patch("tidylinux.installer.desktop_content", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0", approved=True)
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])
        self.assertTrue(downloaded.directory.exists())
        self.assertTrue(self.original.exists())

    def test_cleanup_never_deletes_unknown_files_or_external_directory(self):
        downloaded = self.download()
        (downloaded.directory / "unrelated").write_text("keep")
        with self.assertRaises(TidyError):
            discard_download(self.paths, downloaded.directory)
        self.assertEqual((downloaded.directory / "unrelated").read_text(), "keep")
        with self.assertRaises(TidyError):
            discard_download(self.paths, self.root)
        self.assertTrue(self.original.exists())

    def test_missing_checksum_is_explicit_and_still_size_and_archive_validated(self):
        self.data["assets"][0]["digest"] = None
        self.release = release_from_metadata(REPOSITORY, self.data, "x86_64")
        self.asset = self.release.assets[0]
        self.assertIsNone(self.asset.sha256)
        self.assertTrue(self.download().inspection.source.exists())

    def test_bad_or_duplicate_asset_metadata_is_rejected(self):
        for changes in ({"name": "../escape.zip"}, {"name": "bad\n.zip"}, {"size": -1},
                        {"id": True}, {"digest": "sha256:invalid"}):
            with self.subTest(changes=changes):
                data = metadata(self.content)
                data["assets"][0].update(changes)
                with self.assertRaises(TidyError):
                    release_from_metadata(REPOSITORY, data, "x86_64")

    def test_source_changes_require_a_new_explicit_check(self):
        downloaded = self.download()
        self.sources.configure(self.record, "https://github.com/Another/Source", confirmed=True)
        with self.assertRaises(TidyError):
            install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0", approved=True)
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])

    def test_unknown_downloads_are_only_reported_at_startup_without_network(self):
        unknown = self.paths.downloads / "unrelated"
        unknown.mkdir()
        (unknown / "keep").write_text("keep")
        with mock.patch("tidylinux.updates.build_opener", side_effect=AssertionError("startup network")):
            records, warnings = Registry(self.paths).inventory()
        self.assertEqual(records, [self.record])
        self.assertTrue(any("Temporary update downloads" in item for item in warnings))
        self.assertEqual((unknown / "keep").read_text(), "keep")

    def test_redirect_handler_never_follows_implicitly_and_proxy_credentials_are_not_loaded(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, CDN))
        opener = Opener([(json.dumps(self.data).encode(), {})])
        with mock.patch("tidylinux.updates.build_opener", return_value=opener) as factory:
            result = GitHubClient(machine="x86_64").check(self.record, REPOSITORY, requested=True)
        self.assertEqual(result.status, "Update available")
        handlers = factory.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[2], NoRedirect)

    def test_read_timeout_and_malformed_nested_json_are_source_errors(self):
        class TimeoutResponse(Response):
            def read1(self, *_):
                raise socket.timeout("private URL")
        client, _ = self.client([lambda url: TimeoutResponse(b"", url)])
        self.assertEqual(client.check(self.record, REPOSITORY, requested=True).status, "Unable to check")
        deeply_nested = ("[" * 2000 + "0" + "]" * 2000).encode()
        client, _ = self.client([(deeply_nested, {})])
        self.assertEqual(client.check(self.record, REPOSITORY, requested=True).status, "Unable to check")

    def test_size_and_time_budgets_cannot_be_avoided_by_trickle_responses(self):
        client, _ = self.client([(json.dumps(self.data).encode(), {})])
        with mock.patch("tidylinux.updates.time.monotonic", side_effect=[0, 31]):
            result = client.check(self.record, REPOSITORY, requested=True)
        self.assertEqual(result.status, "Unable to check")
        self.assertIn("timed out", result.detail)
        self.link()
        client, _ = self.client([(self.content, {})])
        with mock.patch("tidylinux.updates.time.monotonic", side_effect=[0, 1801]):
            with self.assertRaisesRegex(TidyError, "timed out"):
                client.download(self.paths, self.record, self.release, self.asset, confirmed=True)
        self.assertEqual(list(self.paths.downloads.iterdir()), [])

    def test_redirect_count_and_encoded_bodies_are_bounded(self):
        redirects = [HTTPError(ASSET, 302, "redirect", {"Location": CDN}, None)] * 6
        client, opener = self.client(redirects)
        with self.assertRaisesRegex(TidyError, "redirect limit"):
            self.download(client)
        self.assertEqual(len(opener.calls), 6)
        client, _ = self.client([(json.dumps(self.data).encode(), {"Content-Encoding": "gzip"})])
        self.assertEqual(client.check(self.record, REPOSITORY, requested=True).status, "Unable to check")

    def test_download_marker_corruption_and_symlinks_are_preserved(self):
        downloaded = self.download()
        marker = downloaded.directory / "download.json"
        metadata_bytes = marker.read_bytes()
        marker.write_text('{"schema": true, "name": "demo.tar.gz"}')
        with self.assertRaises(TidyError):
            discard_download(self.paths, downloaded.directory)
        marker.write_bytes(metadata_bytes)
        source = downloaded.inspection.source
        source.unlink()
        source.symlink_to(self.original)
        with self.assertRaises(TidyError):
            discard_download(self.paths, downloaded.directory)
        self.assertTrue(self.original.exists())

    def test_cleanup_refuses_a_swapped_downloads_parent(self):
        downloaded = self.download()
        moved = self.paths.root / "saved-downloads"
        self.paths.downloads.rename(moved)
        self.paths.downloads.symlink_to(moved)
        with self.assertRaises(TidyError):
            discard_download(self.paths, downloaded.directory)
        self.assertTrue((moved / downloaded.directory.name).is_dir())

    def test_asset_metadata_rejects_duplicate_names_ids_and_invalid_unicode(self):
        for field in ("id", "name"):
            data = metadata(self.content)
            duplicate = dict(data["assets"][0])
            duplicate["id"] = 18
            duplicate["name"] = "other-linux-x64.zip"
            duplicate[field] = data["assets"][0][field]
            data["assets"].append(duplicate)
            with self.subTest(field=field), self.assertRaises(TidyError):
                release_from_metadata(REPOSITORY, data, "x86_64")

    def test_interrupted_online_install_preserves_download_for_recovery_without_network(self):
        from tidylinux import transactions
        class Interrupted(BaseException):
            pass
        downloaded = self.download()
        original_move = transactions.move
        def interrupt(source, destination):
            original_move(source, destination)
            if source == self.paths.apps / self.record.app_id:
                raise Interrupted()
        with mock.patch("tidylinux.transactions.move", side_effect=interrupt):
            with self.assertRaises(Interrupted):
                install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0", approved=True)
        with mock.patch("tidylinux.updates.build_opener", side_effect=AssertionError("recovery network")):
            records, warnings = Registry(self.paths).inventory()
        self.assertEqual(records, [self.record])
        self.assertTrue(warnings)
        self.assertTrue(downloaded.directory.exists())
        self.assertTrue(self.original.exists())

    def test_download_cleanup_failure_does_not_misreport_a_committed_update(self):
        downloaded = self.download()
        with mock.patch("tidylinux.updates.discard_download", side_effect=OSError("cleanup unavailable")):
            updated = install_download(self.installer, self.record, downloaded, "Demo-2/run", "2.0", approved=True)
        self.assertEqual(self.installer.registry.inventory()[0], [updated])
        self.assertTrue(downloaded.directory.exists())

    def test_install_revalidates_identity_and_download_metadata(self):
        downloaded = self.download()
        forged = replace(downloaded, inspection=replace(downloaded.inspection, source=self.original))
        with self.assertRaisesRegex(TidyError, "download metadata changed"):
            install_download(self.installer, self.record, forged, "Demo-2/run", "2.0", approved=True)
        self.assertEqual(self.installer.registry.inventory()[0], [self.record])
        self.assertTrue(self.original.exists())

    def test_one_network_error_does_not_stop_checks_for_other_apps(self):
        self.link()
        second = self.installer.install(inspect_archive(self.original), "Demo-1/run", "Second", "1.0")
        self.sources.configure(second, REPOSITORY, confirmed=True)
        client, opener = self.client([TimeoutError(), (json.dumps(self.data).encode(), {})])
        results = check_installed([self.record, second], self.sources, client, requested=True)
        self.assertEqual([result.status for result in results], ["Unable to check", "Update available"])
        self.assertEqual([request[0] for request in opener.calls], [LATEST, LATEST])

    def test_cancellation_during_timeout_reports_cancelled(self):
        cancel = Event()
        def timeout(_):
            cancel.set()
            raise URLError("timeout after cancel")
        client, _ = self.client([timeout])
        with self.assertRaises(Cancelled):
            client.check(self.record, REPOSITORY, requested=True, cancel=cancel)

    def test_failed_uninstall_restores_linked_source_metadata(self):
        self.link()
        original_unlink = Path.unlink
        def fail(path, *args, **kwargs):
            if path == Path(self.record.desktop_path):
                raise PermissionError("shortcut denied")
            return original_unlink(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", fail):
            with self.assertRaises(PermissionError):
                self.installer.uninstall(self.record)
        self.assertEqual(self.sources.read(self.record), REPOSITORY)
        self.assertEqual(self.installer.registry.inventory(), ([self.record], []))
        for name in ("bad\ud800.zip", "é" * 200 + ".zip"):
            data = metadata(self.content)
            data["assets"][0]["name"] = name
            with self.subTest(name=repr(name)), self.assertRaises(TidyError):
                release_from_metadata(REPOSITORY, data, "x86_64")
