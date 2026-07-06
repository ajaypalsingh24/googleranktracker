from __future__ import annotations

from app.ahrefs import fetch_keyword_volumes as fetch_ahrefs_keyword_volumes
from app.semrush import fetch_keyword_volumes as fetch_semrush_keyword_volumes
from app.semrush import normalize_database


def fetch_keyword_volumes(keywords: list[str], gl: str | None, device: str | None = None) -> tuple[dict[str, int], str | None]:
    ahrefs_volumes = fetch_ahrefs_keyword_volumes(keywords, gl or "in")
    if ahrefs_volumes:
        return ahrefs_volumes, "Ahrefs"

    semrush_volumes = fetch_semrush_keyword_volumes(keywords, normalize_database(gl, device))
    if semrush_volumes:
        return semrush_volumes, "Semrush"

    return {}, None
