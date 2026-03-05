#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

try:
    from .plex_api import PlexHttpClient
    from .plex_config import AppConfig
    from .plex_file_io import write_text_locked
except ImportError:
    from plex_api import PlexHttpClient
    from plex_config import AppConfig
    from plex_file_io import write_text_locked


class PlexPmsClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._library_candidate_cache_path = (
            Path(config.session_store_path).resolve().parent / "library_candidates_cache.json"
        )
        self._http = PlexHttpClient(
            client_identifier=config.client_identifier,
            product=config.product,
            version=config.version,
            platform=config.platform,
            device_name=config.device_name,
        )

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        query_string = f"?{urlencode(query, doseq=True)}" if query else ""
        return f"{self._config.pms_base_url}{normalized_path}{query_string}"

    def get_recent_history(
        self, token: str, account_id: int, limit: int
    ) -> list[dict[str, Any]]:
        # This handles a special case in which the pms owner is accountId 1, 
        # but other users have a different accountId. In that case, we want to get the history for the pms owner, not the user.

        if account_id == 6189978: # accountId for plex owner
            account_id = 1
        
        payload = self._http.request_json(
            "GET",
            self._url(
                "/status/sessions/history/all",
                {"accountID": account_id, "sort": "viewedAt:desc"},
            ),
            token=token,
        )
        metadata = payload.get("MediaContainer", {}).get("Metadata", [])
        if not isinstance(metadata, list):
            return []
        return self._collapse_history_items(metadata, limit)

    def get_metadata(self, token: str, key: str) -> dict[str, Any]:
        payload = self._http.request_json("GET", self._url(key), token=token)
        metadata = payload.get("MediaContainer", {}).get("Metadata", [])
        if isinstance(metadata, list) and metadata:
            return metadata[0]
        return {}

    def get_artwork(self, token: str, path: str) -> tuple[bytes, str]:
        return self._http.request_bytes("GET", self._url(path), token=token)

    def get_enriched_history(
        self, token: str, account_id: int, limit: int
    ) -> list[dict[str, Any]]:
        history_items = self.get_recent_history(token, account_id, limit)
        enriched_items: list[dict[str, Any]] = []
        for item in history_items:
            metadata: dict[str, Any] = {}
            item_key = item.get("key")
            if isinstance(item_key, str) and item_key.startswith("/"):
                metadata = self.get_metadata(token, item_key)
            enriched_items.append(self._merge_history_item(item, metadata))
        return enriched_items

    def get_library_candidates(
        self, token: str, account_id: int, limit: int
    ) -> list[dict[str, Any]]:
        cache_payload = self._load_library_candidate_cache()
        candidates: list[dict[str, Any]] = []
        for media_type in ("1", "2"):
            cached_items = self._get_cached_candidates_for_type(
                cache_payload, media_type, limit
            )
            if self._should_refresh_media_type(
                token, media_type, limit, cache_payload
            ):
                cached_items = self._refresh_media_type_cache(
                    token, media_type, limit, cache_payload
                )
            for candidate in cached_items:
                candidates.append(candidate)

        deduped: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for candidate in candidates:
            #TODO ensure the rating_key is unique across movies and shows, or use a different dedupe key
            dedupe_key = str(candidate.get("rating_key") or candidate.get("title"))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            deduped.append(candidate)
        return deduped[:limit]

    def _fetch_library_metadata(
        self, token: str, media_type: str, limit: int
    ) -> list[dict[str, Any]]:
        payload = self._http.request_json(
            "GET",
            self._url(
                "/library/all",
                {
                    "type": media_type,
                    "sort": "addedAt:desc",
                    "X-Plex-Container-Start": "0",
                    "X-Plex-Container-Size": str(limit),
                },
            ),
            token=token,
        )
        metadata_items = payload.get("MediaContainer", {}).get("Metadata", [])
        if not isinstance(metadata_items, list):
            return []
        return [item for item in metadata_items if isinstance(item, dict)]

    def _should_refresh_media_type(
        self,
        token: str,
        media_type: str,
        limit: int,
        cache_payload: dict[str, Any],
    ) -> bool:
        cache_entry = self._get_cache_entry(cache_payload, media_type)
        cached_items = self._get_cached_candidates_for_type(cache_payload, media_type, limit)
        if not cached_items:
            return True
        stored_items = cache_entry.get("items", [])
        if not isinstance(stored_items, list) or len(stored_items) < limit:
            return True
        cached_highest_key = cache_entry.get("highest_key")
        latest_metadata = self._fetch_library_metadata(token, media_type, 1)
        latest_highest_key = self._highest_metadata_key(latest_metadata)
        if latest_highest_key is None:
            return False
        if cached_highest_key is None:
            return True
        return int(latest_highest_key) > int(cached_highest_key)

    def _refresh_media_type_cache(
        self,
        token: str,
        media_type: str,
        limit: int,
        cache_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        metadata_items = self._fetch_library_metadata(token, media_type, limit)
        items = [self._build_library_candidate(metadata) for metadata in metadata_items]
        media_types = cache_payload.setdefault("media_types", {})
        media_types[media_type] = {
            "highest_key": self._highest_metadata_key(metadata_items),
            "items": items,
        }
        self._write_library_candidate_cache(cache_payload)
        return items

    def _get_cache_entry(
        self, cache_payload: dict[str, Any], media_type: str
    ) -> dict[str, Any]:
        media_types = cache_payload.get("media_types", {})
        if not isinstance(media_types, dict):
            return {}
        entry = media_types.get(media_type, {})
        return entry if isinstance(entry, dict) else {}

    def _get_cached_candidates_for_type(
        self, cache_payload: dict[str, Any], media_type: str, limit: int
    ) -> list[dict[str, Any]]:
        entry = self._get_cache_entry(cache_payload, media_type)
        items = entry.get("items", [])
        if not isinstance(items, list):
            return []
        return [item for item in items[:limit] if isinstance(item, dict)]

    def _load_library_candidate_cache(self) -> dict[str, Any]:
        if not self._library_candidate_cache_path.exists():
            return {"media_types": {}}
        try:
            payload = json.loads(
                self._library_candidate_cache_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {"media_types": {}}
        normalized_payload = payload if isinstance(payload, dict) else {"media_types": {}}
        return normalized_payload

    def _write_library_candidate_cache(self, cache_payload: dict[str, Any]) -> None:
        write_text_locked(
            self._library_candidate_cache_path,
            json.dumps(cache_payload, indent=2),
            encoding="utf-8",
        )

    def _highest_metadata_key(self, metadata_items: list[dict[str, Any]]) -> int | None:
        highest_key: int | None = None
        for metadata in metadata_items:
            metadata_key = self._extract_numeric_key(metadata)
            if metadata_key is None:
                continue
            if highest_key is None or metadata_key > highest_key:
                highest_key = metadata_key
        return highest_key

    def _extract_numeric_key(self, metadata: dict[str, Any]) -> int | None:
        for field_name in ("ratingKey", "parentRatingKey", "parentKey", "key"):
            value = metadata.get(field_name)
            numeric_value = self._parse_numeric_key(value)
            if numeric_value is not None:
                return numeric_value
        return None

    def _parse_numeric_key(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            digits = "".join(character for character in value if character.isdigit())
            if digits:
                return int(digits)
        return None

    def _get_watched_movie_ids(
        self, token: str, account_id: int, limit: int
    ) -> tuple[set[str], set[str]]:
        history_items = self.get_recent_history(token, account_id, limit)
        watched_movie_keys: set[str] = set()
        watched_movie_titles: set[str] = set()
        for item in history_items:
            media_type = str(item.get("type") or "").casefold()
            if media_type != "movie":
                continue
            rating_key = item.get("ratingKey")
            title = item.get("title")
            if rating_key is not None:
                watched_movie_keys.add(str(rating_key))
            if isinstance(title, str) and title:
                watched_movie_titles.add(title.casefold())
        return watched_movie_keys, watched_movie_titles

    def _is_watched_movie(
        self,
        candidate: dict[str, Any],
        watched_movie_keys: set[str],
        watched_movie_titles: set[str],
    ) -> bool:
        if str(candidate.get("media_type") or "").casefold() != "movie":
            return False
        rating_key = candidate.get("rating_key")
        title = candidate.get("title")
        if rating_key is not None and str(rating_key) in watched_movie_keys:
            return True
        return isinstance(title, str) and title.casefold() in watched_movie_titles

    def _merge_history_item(
        self, item: dict[str, Any], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        media_type = metadata.get("type") or item.get("type")
        title = metadata.get("title") or item.get("title") or "Unknown title"
        series_title = metadata.get("grandparentTitle") or item.get("grandparentTitle")
        if media_type == "show" and not series_title:
            series_title = title
        return {
            "history_key": item.get("historyKey"),
            "rating_key": item.get("ratingKey"),
            "media_type": media_type,
            "title": title,
            "series_title": series_title,
            "season_title": metadata.get("parentTitle"),
            "summary": metadata.get("summary"),
            "tagline": metadata.get("tagline"),
            "genres": self._extract_tags(metadata, "Genre"),
            "actors": self._extract_tags(metadata, "Role"),
            "directors": self._extract_tags(metadata, "Director"),
            "writers": self._extract_tags(metadata, "Writer"),
            "cinematographers": self._extract_tags(metadata, "Cinematographer"),
            "year": metadata.get("year"),
            "originally_available_at": metadata.get("originallyAvailableAt")
            or item.get("originallyAvailableAt"),
            "viewed_at": item.get("viewedAt"),
            "art_path": metadata.get("art") or metadata.get("thumb") or item.get("thumb"),
            "thumb_path": metadata.get("thumb") or item.get("thumb"),
        }

    def _collapse_history_items(
        self, metadata_items: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        collapsed_items: list[dict[str, Any]] = []
        seen_show_keys: set[str] = set()
        for item in metadata_items:
            if not isinstance(item, dict):
                continue
            normalized_item = self._normalize_history_item(item)
            if normalized_item is None:
                continue
            show_key = normalized_item.get("_show_dedupe_key")
            if isinstance(show_key, str):
                if show_key in seen_show_keys:
                    continue
                seen_show_keys.add(show_key)
            collapsed_items.append(normalized_item)
            if len(collapsed_items) >= limit:
                break
        return collapsed_items

    def _normalize_history_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        media_type = str(item.get("type") or "").casefold()
        if media_type != "episode":
            return dict(item)

        show_key = self._history_show_key(item)
        if show_key is None:
            return dict(item)

        normalized_item = dict(item)
        normalized_item["_show_dedupe_key"] = show_key
        normalized_item["type"] = "show"
        normalized_item["title"] = (
            item.get("grandparentTitle")
            or item.get("title")
            or "Unknown title"
        )
        normalized_item["key"] = item.get("grandparentKey") or item.get("key")
        grandparent_rating_key = item.get("grandparentRatingKey")
        if grandparent_rating_key is None:
            grandparent_rating_key = self._parse_numeric_key(item.get("grandparentKey"))
        if grandparent_rating_key is not None:
            normalized_item["ratingKey"] = grandparent_rating_key
        return normalized_item

    def _history_show_key(self, item: dict[str, Any]) -> str | None:
        for field_name in ("grandparentKey", "grandparentRatingKey", "grandparentTitle"):
            value = item.get(field_name)
            if value is None:
                continue
            key = str(value).strip()
            if key:
                return key
        return None

    def _build_library_candidate(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "rating_key": metadata.get("ratingKey"),
            "parent_rating_key": metadata.get("parentRatingKey"),
            "parent_key": metadata.get("parentKey"),
            "title": metadata.get("title") or "Unknown title",
            "media_type": metadata.get("type"),
            "year": metadata.get("year"),
            "rating": metadata.get("rating"),
            "rating_image": metadata.get("ratingImage"),
            "audience_rating": metadata.get("audienceRating"),
            "audience_rating_image": metadata.get("audienceRatingImage"),
            "summary": metadata.get("summary"),
            "tagline": metadata.get("tagline"),
            "genres": self._extract_tags(metadata, "Genre"),
            "actors": self._extract_tags(metadata, "Role"),
            "directors": self._extract_tags(metadata, "Director"),
            "writers": self._extract_tags(metadata, "Writer"),
            "cinematographers": self._extract_tags(metadata, "Cinematographer"),
            "originally_available_at": metadata.get("originallyAvailableAt"),
            "added_at": metadata.get("addedAt"),
            "library_section_id": metadata.get("librarySectionID"),
            "library_section_title": metadata.get("librarySectionTitle"),
            "art_path": metadata.get("art"),
            "thumb_path": metadata.get("thumb"),
        }

    def _extract_tags(self, metadata: dict[str, Any], field_name: str) -> list[str]:
        values = metadata.get(field_name, [])
        if not isinstance(values, list):
            return []
        tags: list[str] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            tag = value.get("tag")
            if isinstance(tag, str) and tag not in tags:
                tags.append(tag)
        return tags
