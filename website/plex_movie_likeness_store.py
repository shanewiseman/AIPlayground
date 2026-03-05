#!/usr/bin/env python3

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class MovieLikenessStore:
    def __init__(self, store_path: str, ttl_seconds: int = 3600) -> None:
        self._store_path = Path(store_path)
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}
        persisted_state = self._load_state()
        persisted_sessions = persisted_state.get("sessions", {})
        if isinstance(persisted_sessions, dict):
            self._sessions = {
                session_id: session
                for session_id, session in persisted_sessions.items()
                if isinstance(session_id, str) and isinstance(session, dict)
            }
        self.cleanup()
        self.save()

    def get_state(self, session_id: str) -> dict[str, Any]:
        state = self._get_session_state(session_id)
        if not state:
            return {
                "batch": [],
                "batch_generated_at": None,
                "ratings": {},
                "commonality_summary": "",
                "commonality_updated_at": None,
                "commonality_source_gaps": [],
            }
        return {
            "batch": self._normalize_batch(state.get("batch")),
            "batch_generated_at": (
                state.get("batch_generated_at")
                if isinstance(state.get("batch_generated_at"), str)
                else None
            ),
            "ratings": self._normalize_ratings(state.get("ratings")),
            "commonality_summary": self._normalize_summary(state.get("commonality_summary")),
            "commonality_updated_at": (
                state.get("commonality_updated_at")
                if isinstance(state.get("commonality_updated_at"), str)
                else None
            ),
            "commonality_source_gaps": self._normalize_strings(
                state.get("commonality_source_gaps")
            ),
        }

    def replace_batch(self, session_id: str, batch: list[dict[str, Any]]) -> str:
        state = self._get_or_create_session_state(session_id)
        state["batch"] = self._normalize_batch(batch)
        state["batch_generated_at"] = datetime.now().isoformat()
        self.save()
        return state["batch_generated_at"]

    def save_ratings(self, session_id: str, ratings: dict[str, Any]) -> None:
        state = self._get_or_create_session_state(session_id)
        state["ratings"] = self._normalize_ratings(ratings)
        self.save()

    def save_commonality(
        self,
        session_id: str,
        *,
        commonality_summary: Any,
        source_gaps: Any,
        generated_at: Any,
    ) -> None:
        state = self._get_or_create_session_state(session_id)
        state["commonality_summary"] = self._normalize_summary(commonality_summary)
        state["commonality_source_gaps"] = self._normalize_strings(source_gaps)
        state["commonality_updated_at"] = (
            generated_at if isinstance(generated_at, str) else datetime.now().isoformat()
        )
        self.save()

    def clear_batch(self, session_id: str) -> None:
        state = self._get_session_state(session_id)
        if not state:
            return
        state.pop("batch", None)
        state.pop("batch_generated_at", None)
        if not self._has_persisted_state(state):
            self._sessions.pop(session_id, None)
        self.save()

    def delete(self, session_id: str) -> None:
        if session_id:
            self._sessions.pop(session_id, None)
            self.save()

    def cleanup(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - self._coerce_created_at(session.get("created_at")) > self._ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        if expired:
            self.save()

    def save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sessions": self._sessions}
        self._store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _get_session_state(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        state = self._sessions.get(session_id)
        if not state:
            return None
        if time.time() - self._coerce_created_at(state.get("created_at")) > self._ttl_seconds:
            self._sessions.pop(session_id, None)
            self.save()
            return None
        return state

    def _get_or_create_session_state(self, session_id: str) -> dict[str, Any]:
        state = self._get_session_state(session_id)
        if state is not None:
            return state
        state = {"created_at": time.time()}
        self._sessions[session_id] = state
        return state

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

    def _normalize_batch(self, batch: Any) -> list[dict[str, Any]]:
        if not isinstance(batch, list):
            return []
        return [item for item in batch if isinstance(item, dict)]

    def _normalize_ratings(self, ratings: Any) -> dict[str, int]:
        if not isinstance(ratings, dict):
            return {}
        normalized: dict[str, int] = {}
        for key, value in ratings.items():
            if value in {1, 2, 3, 4, 5}:
                normalized[str(key)] = int(value)
        return normalized

    def _normalize_summary(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    def _normalize_strings(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            trimmed = value.strip()
            if trimmed and trimmed not in normalized:
                normalized.append(trimmed)
        return normalized

    def _has_persisted_state(self, state: dict[str, Any]) -> bool:
        return bool(
            self._normalize_batch(state.get("batch"))
            or self._normalize_ratings(state.get("ratings"))
            or self._normalize_summary(state.get("commonality_summary"))
        )

    def _coerce_created_at(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return 0.0
