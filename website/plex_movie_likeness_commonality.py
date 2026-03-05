#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    from .plex_file_io import write_text_locked
except ImportError:
    from plex_file_io import write_text_locked


class MovieLikenessCommonalityError(RuntimeError):
    pass


class MovieCommonalityNarrative(BaseModel):
    commonality_summary: str = Field(
        description=(
            "A concise summary of the shared themes, tones, genres, or story qualities "
            "across the positively rated movies. This string is intended for later recommendation use."
        )
    )
    source_gaps: list[str] = Field(
        description="Short caveats about missing metadata or limited evidence."
    )


class MovieLikenessCommonalityService:
    def __init__(self) -> None:
        self._agents_src = (
            Path(__file__).resolve().parent.parent / "openai-agents-python" / "src"
        )
        debug_output_dir = Path(__file__).resolve().parent / "debug_output"
        self._prompt_dump_path = (
            debug_output_dir / "movie_likeness_commonality_prompt_submission.json"
        )
        self._final_output_dump_path = (
            debug_output_dir / "movie_likeness_commonality_final_output.json"
        )

    def update_commonality(
        self,
        *,
        current_commonality_summary: str,
        rated_movies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        if not rated_movies:
            return {
                "schema_version": "plex_movie_likeness_commonality.v1",
                "generated_at": generated_at,
                "commonality_summary": current_commonality_summary,
                "source_gaps": ["No newly rated movies were supplied for commonality updates."],
            }

        narrative = self._generate_commonality_summary(
            current_commonality_summary=current_commonality_summary,
            rated_movies=rated_movies,
        )
        return {
            "schema_version": "plex_movie_likeness_commonality.v1",
            "generated_at": generated_at,
            "commonality_summary": narrative.commonality_summary,
            "source_gaps": narrative.source_gaps,
        }

    def _generate_commonality_summary(
        self,
        *,
        current_commonality_summary: str,
        rated_movies: list[dict[str, Any]],
    ) -> MovieCommonalityNarrative:
        if not os.environ.get("OPENAI_API_KEY"):
            raise MovieLikenessCommonalityError(
                "OPENAI_API_KEY is not set. Set it before submitting /movie-likeness."
            )

        Agent, Runner, set_tracing_disabled = self._load_agents_sdk()
        set_tracing_disabled(True)

        model_name = os.environ.get("OPENAI_AGENT_MODEL", "gpt-5-mini")
        agent = Agent(
            name="PlexMovieLikenessCommonality",
            model=model_name,
            instructions=(
                "You maintain a compact evolving commonality summary for a user's positively rated movies. "
                "Use only the provided current_commonality_summary and newly_rated_movies. "
                "Each movie includes title, summary, genres, and a 1-5 likeness rating. "
                "Treat higher ratings as stronger evidence of durable preference. "
                "Update the summary incrementally instead of rewriting it from scratch unless the current "
                "summary is empty or contradicted by the new evidence. "
                "Focus on recurring genres, tone, story context, pacing, setting, and thematic overlap. "
                "Do not mention implementation details, score arithmetic, or specific recommendation titles. "
                "Return one concise paragraph in commonality_summary plus brief source_gaps."
            ),
            output_type=MovieCommonalityNarrative,
        )
        prompt = json.dumps(
            {
                "task": (
                    "Update the evolving movie commonality summary using the current summary "
                    "and the newly rated movies."
                ),
                "current_commonality_summary": current_commonality_summary,
                "newly_rated_movies": rated_movies,
            },
            indent=2,
        )
        self._write_prompt_dump(prompt)
        try:
            result = Runner.run_sync(agent, prompt)
        except Exception as exc:  # pragma: no cover - runtime integration path
            raise MovieLikenessCommonalityError(
                f"OpenAI movie commonality generation failed: {exc}"
            ) from exc
        final_output = result.final_output
        self._write_final_output_dump(final_output)
        return final_output

    def _write_prompt_dump(self, prompt: str) -> None:
        write_text_locked(self._prompt_dump_path, prompt, encoding="utf-8")

    def _write_final_output_dump(self, final_output: Any) -> None:
        model_dump_json = getattr(final_output, "model_dump_json", None)
        if callable(model_dump_json):
            output_text = str(model_dump_json(indent=2))
        else:
            output_text = str(final_output)
        write_text_locked(self._final_output_dump_path, output_text, encoding="utf-8")

    def _load_agents_sdk(self) -> tuple[Any, Any, Any]:
        if not self._agents_src.exists():
            raise MovieLikenessCommonalityError(
                f"Agents SDK source directory was not found at {self._agents_src}."
            )
        agents_src = str(self._agents_src)
        if agents_src not in sys.path:
            sys.path.insert(0, agents_src)
        try:
            from agents import Agent, Runner, set_tracing_disabled
        except Exception as exc:  # pragma: no cover - environment-specific import path
            raise MovieLikenessCommonalityError(
                f"Could not import the local openai-agents-python SDK: {exc}"
            ) from exc
        return Agent, Runner, set_tracing_disabled
