#!/usr/bin/env python3

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

try:
    from .plex_api import PlexHttpClient
    from .plex_config import AppConfig
except ImportError:
    from plex_api import PlexHttpClient
    from plex_config import AppConfig


PLEX_PINS_URL = "https://plex.tv/api/v2/pins"
PLEX_AUTH_URL = "https://app.plex.tv/auth"
PLEX_USER_URL = "https://plex.tv/api/v2/user"


class PlexTvClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._http = PlexHttpClient(
            client_identifier=config.client_identifier,
            product=config.product,
            version=config.version,
            platform=config.platform,
            device_name=config.device_name,
        )

    def create_pin(self) -> dict[str, Any]:
        return self._http.request_json("POST", f"{PLEX_PINS_URL}?strong=true")

    def get_pin(self, pin_id: int) -> dict[str, Any]:
        return self._http.request_json("GET", f"{PLEX_PINS_URL}/{pin_id}")

    def build_login_url(self, code: str) -> str:
        params = {
            "clientID": self._config.client_identifier,
            "code": code,
            "forwardUrl": self._config.callback_url,
            "context[device][product]": self._config.product,
            "context[device][version]": self._config.version,
            "context[device][platform]": self._config.platform,
            "context[device][deviceName]": self._config.device_name,
        }
        return f"{PLEX_AUTH_URL}#?{urlencode(params)}"

    def get_user_profile(self, token: str) -> dict[str, Any]:
        payload = self._http.request_json("GET", PLEX_USER_URL, token=token)
        return {
            "username": payload.get("username") or payload.get("title"),
            "account_id": payload.get("id"),
        }
