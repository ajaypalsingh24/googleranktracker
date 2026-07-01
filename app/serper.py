from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

SERPER_ENDPOINT = "https://google.serper.dev/search"


def normalize_host(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    return (parsed.hostname or value).lower().removeprefix("www.")


def matches_domain(link: str, domain: str) -> bool:
    link_host = normalize_host(link)
    project_host = normalize_host(domain)
    return link_host == project_host or link_host.endswith(f".{project_host}")


def check_keyword_rank(keyword: dict) -> dict:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key or api_key == "PASTE_YOUR_SERPER_API_KEY_HERE":
        raise RuntimeError("Add your real SERPER_API_KEY in the .env file before checking rankings.")

    payload = {
        "q": keyword["phrase"],
        "gl": keyword["gl"],
        "hl": keyword["hl"],
        "location": keyword["location"],
        "num": 100,
    }
    response = httpx.post(
        SERPER_ENDPOINT,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    organic = data.get("organic") if isinstance(data.get("organic"), list) else []
    match = next((item for item in organic if item.get("link") and matches_domain(item["link"], keyword["domain"])), None)

    return {
        "position": match.get("position") if match else None,
        "matched_url": match.get("link") if match else None,
        "result_count": len(organic),
        "raw": data,
        "organic": [
            {
                "position": item.get("position") or index + 1,
                "title": item.get("title") or "",
                "link": item.get("link") or "",
                "display_link": item.get("displayLink") or item.get("domain") or "",
                "snippet": item.get("snippet") or "",
            }
            for index, item in enumerate(organic)
        ],
    }
