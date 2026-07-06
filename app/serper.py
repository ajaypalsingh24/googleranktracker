from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

SERPER_ENDPOINT = "https://google.serper.dev/search"
SERPER_PLACES_ENDPOINT = "https://google.serper.dev/places"


def normalize_host(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    return (parsed.hostname or value).lower().removeprefix("www.")


def matches_domain(link: str, domain: str) -> bool:
    link_host = normalize_host(link)
    project_host = normalize_host(domain)
    return link_host == project_host or link_host.endswith(f".{project_host}")


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def matches_business_name(title: str, business_name: str) -> bool:
    title_name = normalize_name(title)
    project_name = normalize_name(business_name)
    return bool(project_name and (project_name in title_name or title_name in project_name))


def check_keyword_rank(keyword: dict) -> dict:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key or api_key == "PASTE_YOUR_SERPER_API_KEY_HERE":
        raise RuntimeError("Add your real SERPER_API_KEY in the .env file before checking rankings.")

    if keyword.get("project_type") == "local":
        return check_local_rank(keyword, api_key)

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
        "search_type": "organic",
        "position": match.get("position") if match else None,
        "matched_title": match.get("title") if match else None,
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


def check_local_rank(keyword: dict, api_key: str) -> dict:
    payload = {
        "q": keyword["phrase"],
        "gl": keyword["gl"],
        "hl": keyword["hl"],
        "location": keyword.get("search_location") or keyword["location"],
        "num": 20,
    }
    response = httpx.post(
        SERPER_PLACES_ENDPOINT,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    places = data.get("places") if isinstance(data.get("places"), list) else []
    normalized = [normalize_place(item, index) for index, item in enumerate(places)]
    match = next((item for item in normalized if matches_local_result(item, keyword)), None)

    return {
        "search_type": "local",
        "position": match.get("position") if match else None,
        "matched_title": match.get("title") if match else None,
        "matched_url": match.get("link") if match else None,
        "result_count": len(normalized),
        "raw": data,
        "organic": normalized,
    }


def normalize_place(item: dict, index: int) -> dict:
    website = item.get("website") or item.get("link") or item.get("url") or ""
    address = item.get("address") or item.get("formattedAddress") or ""
    rating = item.get("rating") or ""
    reviews = item.get("reviews") or item.get("reviewsCount") or ""
    details = " ".join(str(part) for part in [address, f"Rating {rating}" if rating else "", f"Reviews {reviews}" if reviews else ""] if part)
    return {
        "position": item.get("position") or index + 1,
        "title": item.get("title") or item.get("name") or "",
        "link": website,
        "display_link": item.get("category") or item.get("type") or "Google Local",
        "snippet": details,
    }


def matches_local_result(item: dict, keyword: dict) -> bool:
    business_name = keyword.get("local_business_name") or ""
    if business_name and matches_business_name(item.get("title", ""), business_name):
        return True
    link = item.get("link") or ""
    return bool(link and matches_domain(link, keyword["domain"]))
