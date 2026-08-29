"""Safe resolution for files served by the frontend SPA fallback."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


def resolve_spa_file(dist_dir: str | Path, full_path: str) -> Path:
    """Return a safe SPA asset path, falling back to the canonical index file.

    The client-controlled path is decoded before validation so encoded traversal
    sequences cannot escape the distribution directory.  Only existing regular
    files below the canonical distribution root are returned.
    """
    root = Path(dist_dir).resolve()
    index_html = (root / "index.html").resolve()
    requested = unquote(full_path)

    if not requested or "\\" in requested:
        return index_html

    try:
        candidate = Path(requested)
        if candidate.is_absolute():
            return index_html
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return index_html

    if resolved.is_file():
        return resolved
    return index_html
