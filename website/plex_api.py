#!/usr/bin/env python3

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PlexApiError(RuntimeError):
    pass


class PlexHttpClient:
    def __init__(
        self,
        *,
        client_identifier: str,
        product: str,
        version: str,
        platform: str,
        device_name: str,
    ) -> None:
        self._client_identifier = client_identifier
        self._product = product
        self._version = version
        self._platform = platform
        self._device_name = device_name

    def headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Plex-Client-Identifier": self._client_identifier,
            "X-Plex-Product": self._product,
            "X-Plex-Version": self._version,
            "X-Plex-Platform": self._platform,
            "X-Plex-Device-Name": self._device_name,
        }
        if token:
            headers["X-Plex-Token"] = token
        return headers

    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = self.headers(token)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url=url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise PlexApiError(
                f"Plex API returned {exc.code} for {url}: {details}"
            ) from exc
        except URLError as exc:
            raise PlexApiError(f"Could not reach Plex API at {url}: {exc}") from exc

    def request_bytes(
        self,
        method: str,
        url: str,
        *,
        token: str,
        accept: str = "*/*",
    ) -> tuple[bytes, str]:
        headers = self.headers(token)
        headers["Accept"] = accept
        request = Request(url=url, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                content_type = (
                    response.headers.get_content_type() or "application/octet-stream"
                )
                return response.read(), content_type
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise PlexApiError(
                f"Plex API returned {exc.code} for {url}: {details}"
            ) from exc
        except URLError as exc:
            raise PlexApiError(f"Could not reach Plex API at {url}: {exc}") from exc
