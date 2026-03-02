#!/usr/bin/env python3

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    base_url: str
    client_identifier: str
    product: str
    version: str
    platform: str
    device_name: str
    pms_base_url: str
    session_secret: str | None
    session_store_path: str
    pin_poll_attempts: int
    pin_poll_interval_seconds: float
    history_item_limit: int
    library_candidate_limit: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        host = os.environ.get("HOST", "127.0.0.1")
        port = int(os.environ.get("PORT", "8000"))
        base_url = os.environ.get("APP_BASE_URL", f"http://{host}:{port}").rstrip("/")
        default_session_store_path = (
            Path(__file__).resolve().parent / "session_store.json"
        )
        return cls(
            host=host,
            port=port,
            base_url=base_url,
            client_identifier=os.environ.get(
                "PLEX_CLIENT_IDENTIFIER", "openai-playground-plex-demo"
            ),
            product=os.environ.get("PLEX_PRODUCT", "OpenAI Playground Plex Demo"),
            version=os.environ.get("PLEX_VERSION", "0.1.0"),
            platform=os.environ.get("PLEX_PLATFORM", "Web"),
            device_name=os.environ.get("PLEX_DEVICE_NAME", "OpenAI Playground"),
            pms_base_url=os.environ.get(
                "PLEX_PMS_BASE_URL", "http://10.1.0.67:32400"
            ).rstrip("/"),
            session_secret=os.environ.get("SESSION_SECRET"),
            session_store_path=os.environ.get(
                "SESSION_STORE_PATH", str(default_session_store_path)
            ),
            pin_poll_attempts=int(os.environ.get("PLEX_PIN_POLL_ATTEMPTS", "12")),
            pin_poll_interval_seconds=float(
                os.environ.get("PLEX_PIN_POLL_INTERVAL_SECONDS", "1.0")
            ),
            history_item_limit=int(os.environ.get("PLEX_HISTORY_ITEM_LIMIT", "100")),
            library_candidate_limit=int(
                os.environ.get("PLEX_LIBRARY_CANDIDATE_LIMIT", "4000")
            ),
        )

    @property
    def callback_url(self) -> str:
        return f"{self.base_url}/auth/plex/callback"
