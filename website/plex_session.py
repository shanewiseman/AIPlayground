#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from typing import Any

try:
    from .plex_file_io import write_text_locked
except ImportError:
    from plex_file_io import write_text_locked


class SessionStore:
    def __init__(
        self, secret: str | None, store_path: str, ttl_seconds: int = 3600
    ) -> None:
        self._store_path = Path(store_path)
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}
        persisted_state = self._load_state()
        resolved_secret = secret or persisted_state.get("secret") or secrets.token_hex(32)
        self._secret = resolved_secret.encode("utf-8")
        persisted_sessions = persisted_state.get("sessions", {})
        if isinstance(persisted_sessions, dict):
            self._sessions = {
                session_id: session
                for session_id, session in persisted_sessions.items()
                if isinstance(session_id, str) and isinstance(session, dict)
            }
        self.cleanup()
        self.save()

    def create(self) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = {"created_at": time.time()}
        self.save()
        return session_id

    def get(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        if time.time() - session.get("created_at", 0) > self._ttl_seconds:
            self.delete(session_id)
            return None
        return session

    def delete(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)
            self.save()

    def sign(self, value: str) -> str:
        digest = hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{value}.{digest}"

    def unsign(self, value: str | None) -> str | None:
        if not value or "." not in value:
            return None
        session_id, digest = value.rsplit(".", 1)
        expected = hmac.new(
            self._secret, session_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(digest, expected):
            return None
        return session_id

    def cleanup(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.get("created_at", 0) > self._ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        if expired:
            self.save()

    def save(self) -> None:
        payload = {
            "secret": self._secret.decode("utf-8"),
            "sessions": self._sessions,
        }
        write_text_locked(
            self._store_path,
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _load_state(self) -> dict[str, Any]:
        if not self._store_path.exists():
            return {}
        try:
            raw = self._store_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload
