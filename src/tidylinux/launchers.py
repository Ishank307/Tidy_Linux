"""Explain launcher choices using filenames only; never execute bundle contents."""
from pathlib import PurePosixPath
import re

from .models import Inspection


def helper_reason(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    if name in {"chrome-sandbox", "chrome_crashpad_handler", "crashpad_handler", "nacl_helper", "qtwebengineprocess"}:
        return "Helper program for sandboxing or background services; choose the main app instead."
    if name.endswith(("_helper", "-helper")) or any(p.lower() in {"node_modules", "extensions", "helpers"} for p in parts[:-1]):
        return "Bundled helper or component; usually not the main application."
    return None


def recommend_launcher(inspection: Inspection) -> tuple[str | None, str]:
    eligible = [c for c in inspection.candidates if not helper_reason(c.path)]
    if len(inspection.candidates) == 1 and eligible:
        return eligible[0].path, "This package has one application launcher."
    if not eligible:
        return None, "Only helper programs were identified. Check the publisher's instructions for the application launcher."
    apprun = [c for c in eligible if PurePosixPath(c.path).name == "AppRun" and len(PurePosixPath(c.path).parts) <= 2]
    if len(apprun) == 1:
        return apprun[0].path, "AppRun is the standard launcher for this application bundle."
    root = (inspection.top_directory or inspection.suggested_name).lower()
    matches = []
    for candidate in eligible:
        name = PurePosixPath(candidate.path).stem.lower()
        suffix = root[len(name):] if root.startswith(name) else None
        if len(name) >= 3 and suffix is not None and (not suffix or re.match(r"^[\s._-](?:linux|x86|x64|amd64|aarch64|arm|\d)", suffix)):
            matches.append(candidate)
        elif name == "code" and root.startswith(("vscode-", "vscode_", "vscode ")):
            matches.append(candidate)
    if matches:
        depth = min(len(PurePosixPath(c.path).parts) for c in matches)
        matches = [c for c in matches if len(PurePosixPath(c.path).parts) == depth]
        if len(matches) == 1:
            return matches[0].path, "Its name matches the application bundle. Other executables may be supporting tools."
    depth = min(len(PurePosixPath(c.path).parts) for c in eligible)
    shallow = [c for c in eligible if len(PurePosixPath(c.path).parts) == depth]
    if len(shallow) == 1 and depth <= 2:
        return shallow[0].path, "This is the only launcher at the top of the bundle after excluding known helpers."
    return None, "The main launcher could not be identified confidently. Check the publisher's launch instructions, then choose the matching file below."
