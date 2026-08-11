from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests


@dataclass
class SteamWorkshopInfo:
    title: str | None = None
    author: str | None = None
    subscriptions: int | None = None
    rating: float | None = None
    description: str | None = None
    image_url: str | None = None
    tags: list[str] | None = None


class SteamClient:
    API_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
    PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"

    def __init__(self, timeout: int = 20):
        self.session = requests.Session()
        self.timeout = timeout

    def fetch(self, workshop_id: str) -> SteamWorkshopInfo:
        info = self._fetch_api(workshop_id)
        page_info = self._fetch_page(workshop_id)
        return SteamWorkshopInfo(
            title=info.title or page_info.title,
            author=info.author or page_info.author,
            subscriptions=info.subscriptions or page_info.subscriptions,
            rating=info.rating or page_info.rating,
            description=info.description or page_info.description,
            image_url=info.image_url or page_info.image_url,
            tags=info.tags if info.tags is not None else page_info.tags,
        )

    def _fetch_api(self, workshop_id: str) -> SteamWorkshopInfo:
        try:
            response = self.session.post(
                self.API_URL,
                data={"itemcount": 1, "publishedfileids[0]": workshop_id},
                timeout=self.timeout,
            )
            response.raise_for_status()
            details = response.json()["response"]["publishedfiledetails"][0]
        except Exception:
            return SteamWorkshopInfo()

        return SteamWorkshopInfo(
            title=details.get("title"),
            author=details.get("creator") or None,
            subscriptions=_to_int(details.get("subscriptions")),
            description=_strip_html(details.get("description") or ""),
            image_url=details.get("preview_url"),
            tags=_extract_tags(details.get("tags")),
        )

    def _fetch_page(self, workshop_id: str) -> SteamWorkshopInfo:
        try:
            response = self.session.get(self.PAGE_URL.format(workshop_id=workshop_id), timeout=self.timeout)
            response.raise_for_status()
            text = response.text
        except Exception:
            return SteamWorkshopInfo()

        title = _first_match(text, r'<div class="workshopItemTitle">(.+?)</div>')
        author = _first_match(text, r'<div class="friendBlockContent">(.+?)<br>')
        subs_text = _first_match(text, r'([\d,]+)\s+Current Subscribers')
        rating_width = _first_match(text, r'fileRatingDetails.*?width:\s*(\d+)px')
        return SteamWorkshopInfo(
            title=_clean(title),
            author=_clean(author),
            subscriptions=_to_int(subs_text.replace(",", "") if subs_text else None),
            rating=round((int(rating_width) / 16), 1) if rating_width and rating_width.isdigit() else None,
        )


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return html.unescape(re.sub(r"<.+?>", "", value)).strip() or None


def _strip_html(value: str) -> str:
    return _clean(value) or ""


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_tags(value) -> list[str]:
    """Return the creator-selected Workshop tags from Steam's API response."""
    if not isinstance(value, list):
        return []
    tags = []
    for entry in value:
        tag = entry.get("tag") if isinstance(entry, dict) else entry
        if isinstance(tag, str) and tag.strip():
            tags.append(tag.strip())
    return list(dict.fromkeys(tags))
