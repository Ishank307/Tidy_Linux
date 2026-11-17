"""Shared values; deliberately independent of GTK."""
from dataclasses import dataclass, asdict
from pathlib import Path


class TidyError(Exception):
    """An actionable error suitable for display to a user."""


class Cancelled(TidyError):
    pass


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str


@dataclass(frozen=True)
class Inspection:
    source: Path
    archive_size: int
    checksum: str
    expanded_size: int
    entries: int
    candidates: tuple[Candidate, ...]
    icons: tuple[str, ...]
    top_directory: str | None
    suggested_name: str
    format: str = "archive"


@dataclass(frozen=True)
class AppRecord:
    app_id: str
    name: str
    version: str
    install_dir: str
    executable: str
    icon: str | None
    source: str
    source_size: int
    checksum: str
    installed_at: str
    desktop_path: str
    desktop_checksum: str
    installed_size: int
    schema: int = 1

    def to_dict(self) -> dict:
        return asdict(self)
