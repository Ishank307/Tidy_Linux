"""Local, identity-bound update source metadata. This module never uses a network."""
from pathlib import Path
import json
import re

from .models import AppRecord, TidyError
from .registry import Paths, Registry
from .transactions import atomic_write, read_json, require_recovered, sync


def repository_url(value: str) -> str:
    if not isinstance(value, str):
        raise TidyError("Enter https://github.com/OWNER/REPOSITORY.")
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})/?", value)
    if (not match or match[2] in {".", ".."} or match[2].endswith(".git")
            or match[1].endswith("-") or "--" in match[1]):
        raise TidyError("Use a public https://github.com/OWNER/REPOSITORY URL, without credentials, query, or fragment.")
    return f"https://github.com/{match[1]}/{match[2]}"


class SourceStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    def path(self, record: AppRecord) -> Path:
        # A caller cannot attach metadata to an external or stale installation.
        current = Registry(self.paths).load(self.paths.apps / record.app_id)
        if current != record:
            raise TidyError("Application record changed. Refresh before configuring its source.")
        return self.paths.sources / f"{record.app_id}.json"

    def read(self, record: AppRecord) -> str | None:
        self.paths.prepare()
        path = self.path(record)
        if not path.exists() and not path.is_symlink():
            return None
        data = read_json(path)
        if (set(data) != {"schema", "app_id", "repository"} or type(data["schema"]) is not int
                or data["schema"] != 1 or data["app_id"] != record.app_id):
            raise TidyError("Update source metadata is invalid; it was left untouched.")
        url = repository_url(data["repository"])
        if url != data["repository"]:
            raise TidyError("Update source metadata is not canonical.")
        return url

    def configure(self, record: AppRecord, url: str | None, *, confirmed: bool = False) -> None:
        if not confirmed:
            raise TidyError("Linking or removing an update source requires explicit confirmation.")
        canonical = repository_url(url) if url is not None else None
        with self.paths.locked():
            require_recovered(self.paths)
            path = self.path(record)
            self.read(record)  # Refuse to overwrite malformed or unrelated metadata.
            if canonical is None:
                path.unlink(missing_ok=True)
                sync(path.parent)
            else:
                atomic_write(path, json.dumps({"schema": 1, "app_id": record.app_id,
                                               "repository": canonical}).encode())
            sync(self.paths.root)  # Persist the sources directory entry as well.
