from __future__ import annotations

import os
from typing import Any

import httpx

AHREFS_KEYWORDS_ENDPOINT = "https://api.ahrefs.com/v3/keywords-explorer/overview"


def fetch_keyword_volumes(keywords: list[str], country: str = "in") -> dict[str, int]:
    api_key = os.getenv("AHREFS_API_KEY")
    if not api_key:
        return {}

    clean_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    if not clean_keywords:
        return {}

    volumes: dict[str, int] = {}
    for chunk in chunked(clean_keywords, 100):
        response = httpx.get(
            AHREFS_KEYWORDS_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            params={
                "country": normalize_country(country),
                "select": "keyword,volume",
                "keywords": ",".join(chunk),
            },
            timeout=45,
        )
        if response.status_code in {400, 401, 403, 404}:
            return volumes
        response.raise_for_status()
        volumes.update(parse_volume_response(response.json()))
    return volumes


def normalize_country(value: str | None) -> str:
    country = (value or "in").strip().lower()
    return "gb" if country == "uk" else country


def parse_volume_response(data: Any) -> dict[str, int]:
    volumes: dict[str, int] = {}
    rows = data.get("keywords") or data.get("data") or data.get("rows") or []
    if isinstance(rows, dict):
        rows = rows.values()
    if not isinstance(rows, list):
        return volumes
    for row in rows:
        if not isinstance(row, dict):
            continue
        keyword = str(row.get("keyword") or row.get("phrase") or "").strip().lower()
        volume = row.get("volume") or row.get("search_volume") or row.get("searchVolume")
        if keyword and isinstance(volume, int):
            volumes[keyword] = volume
        elif keyword and isinstance(volume, str) and volume.isdigit():
            volumes[keyword] = int(volume)
    return volumes


def chunked(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
