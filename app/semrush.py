from __future__ import annotations

import csv
import os
from io import StringIO

import httpx

SEMRUSH_ENDPOINT = "https://api.semrush.com/"


def normalize_database(gl: str | None, device: str | None = None) -> str:
    database = (gl or "in").strip().lower()
    if database == "gb":
        database = "uk"
    if device == "mobile":
        mobile_database = f"mobile-{database}"
        if mobile_database in {"mobile-us", "mobile-uk", "mobile-ca", "mobile-de", "mobile-fr", "mobile-es", "mobile-it", "mobile-br", "mobile-au", "mobile-dk", "mobile-mx", "mobile-nl", "mobile-se", "mobile-tr", "mobile-in", "mobile-id", "mobile-il"}:
            return mobile_database
    return database


def fetch_keyword_volumes(keywords: list[str], database: str = "in") -> dict[str, int]:
    api_key = os.getenv("SEMRUSH_API_KEY")
    if not api_key:
        return {}

    volumes: dict[str, int] = {}
    clean_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    for chunk in chunked(clean_keywords, 100):
        response = httpx.get(
            SEMRUSH_ENDPOINT,
            params={
                "type": "phrase_these",
                "key": api_key,
                "phrase": ";".join(chunk),
                "database": database,
                "export_columns": "Ph,Nq",
            },
            timeout=45,
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text or text.upper().startswith("ERROR"):
            continue
        reader = csv.DictReader(StringIO(text), delimiter=";")
        for row in reader:
            keyword = (row.get("Keyword") or row.get("keyword") or "").strip().lower()
            volume = row.get("Search Volume") or row.get("volume") or ""
            if keyword and str(volume).isdigit():
                volumes[keyword] = int(volume)
    return volumes


def chunked(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
