#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    from .plex_agent_instructions import PLEX_VIEWING_SUMMARY_INSTRUCTIONS
except ImportError:
    from plex_agent_instructions import PLEX_VIEWING_SUMMARY_INSTRUCTIONS


class ViewingSummaryError(RuntimeError):
    pass


class PersonFrequency(BaseModel):
    name: str = Field(description="Person name exactly as found in the Plex metadata.")
    appearances: int = Field(description="How many watched items this person appeared in.")
    titles: list[str] = Field(description="Titles associated with this person in the watched history.")


class NarrativeSummary(BaseModel):
    executive_summary: str = Field(
        description="A concise overall summary of the viewer's recent content preferences."
    )
    plot_context_observations: list[str] = Field(
        description="Short observations about recurring plot themes, settings, or story context."
    )
    viewer_profile_tags: list[str] = Field(
        description="Compact labels that could be stored in a database to describe taste."
    )
    source_gaps: list[str] = Field(
        description="Data gaps or caveats in the provided metadata, such as missing cinematographer credits."
    )


class PlexHistorySummaryService:
    def __init__(self) -> None:
        self._agents_src = (
            Path(__file__).resolve().parent.parent / "openai-agents-python" / "src"
        )

    def summarize(
        self, *, account_id: int, history_items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not history_items:
            return {
                "schema_version": "plex_viewing_summary.v1",
                "account_id": account_id,
                "item_count": 0,
                "content_type_breakdown": [],
                "recurring_actors": [],
                "recurring_directors": [],
                "recurring_cinematographers": [],
                "viewer_profile_tags": [],
                "plot_context_observations": [],
                "executive_summary": "No recent watched items were available for analysis.",
                "source_gaps": ["No playback history was available in the provided input."],
                "watched_items": [],
            }

        narrative = self._generate_narrative_summary(history_items)
        summary_object = {
            "schema_version": "plex_viewing_summary.v1",
            "account_id": account_id,
            "item_count": len(history_items),
            "content_type_breakdown": self._content_type_breakdown(history_items),
            "recurring_actors": self._aggregate_people(history_items, "actors"),
            "recurring_directors": self._aggregate_people(history_items, "directors"),
            "recurring_cinematographers": self._aggregate_people(
                history_items, "cinematographers"
            ),
            "viewer_profile_tags": narrative.viewer_profile_tags,
            "plot_context_observations": narrative.plot_context_observations,
            "executive_summary": narrative.executive_summary,
            "source_gaps": self._combine_gaps(history_items, narrative.source_gaps),
            "watched_items": [
                {
                    "title": item.get("title"),
                    "media_type": item.get("media_type"),
                    "series_title": item.get("series_title"),
                    "season_title": item.get("season_title"),
                    "year": item.get("year"),
                    "viewed_at": item.get("viewed_at"),
                    "originally_available_at": item.get("originally_available_at"),
                    "plot_context": item.get("summary"),
                    #"actors": item.get("actors", []),
                    #"directors": item.get("directors", []),
                    #"cinematographers": item.get("cinematographers", []),
                    "genres": item.get("genres", []),
                    "rating_key": item.get("rating_key"),
                }
                for item in history_items
            ],
        }
        return summary_object

    def _generate_narrative_summary(
        self, history_items: list[dict[str, Any]]
    ) -> NarrativeSummary:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ViewingSummaryError(
                "OPENAI_API_KEY is not set. Set it before opening /summary."
            )

        Agent, Runner, set_tracing_disabled = self._load_agents_sdk()
        set_tracing_disabled(True)

        model_name = os.environ.get("OPENAI_AGENT_MODEL", "gpt-5-mini")
        agent = Agent(
            name="PlexViewingSummary",
            model=model_name,
            instructions=PLEX_VIEWING_SUMMARY_INSTRUCTIONS,
            output_type=NarrativeSummary,
        )
        prompt = json.dumps(
            {
                "task": "Summarize recent viewing history into a structured narrative object.",
                "history_items": history_items,
            },
            indent=2,
        )
        try:
            result = Runner.run_sync(agent, prompt)
        except Exception as exc:  # pragma: no cover - runtime integration path
            raise ViewingSummaryError(f"OpenAI summary generation failed: {exc}") from exc
        return result.final_output

    def _load_agents_sdk(self) -> tuple[Any, Any, Any]:
        if not self._agents_src.exists():
            raise ViewingSummaryError(
                f"Agents SDK source directory was not found at {self._agents_src}."
            )
        agents_src = str(self._agents_src)
        if agents_src not in sys.path:
            sys.path.insert(0, agents_src)
        try:
            from agents import Agent, Runner, set_tracing_disabled
        except Exception as exc:  # pragma: no cover - environment-specific import path
            raise ViewingSummaryError(
                f"Could not import the local openai-agents-python SDK: {exc}"
            ) from exc
        return Agent, Runner, set_tracing_disabled

    def _content_type_breakdown(
        self, history_items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        counts = Counter(str(item.get("media_type") or "unknown") for item in history_items)
        return [
            {"media_type": media_type, "count": count}
            for media_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _aggregate_people(
        self, history_items: list[dict[str, Any]], field_name: str
    ) -> list[dict[str, Any]]:
        appearances: dict[str, int] = {}
        titles_by_person: dict[str, list[str]] = {}
        for item in history_items:
            title = str(item.get("title") or "Unknown title")
            names = item.get(field_name, [])
            if not isinstance(names, list):
                continue
            seen_for_item: set[str] = set()
            for name in names:
                if not isinstance(name, str) or not name or name in seen_for_item:
                    continue
                seen_for_item.add(name)
                appearances[name] = appearances.get(name, 0) + 1
                titles = titles_by_person.setdefault(name, [])
                if title not in titles:
                    titles.append(title)
        people = [
            PersonFrequency(name=name, appearances=count, titles=titles_by_person[name]).model_dump()
            # Only include people who appear in more than two watched items to focus on recurring collaborators.
            for name, count in sorted(appearances.items(), key=lambda item: (-item[1], item[0])) if count > 2
        ]
        return people

    def _combine_gaps(
        self, history_items: list[dict[str, Any]], narrative_gaps: list[str]
    ) -> list[str]:
        gaps = list(narrative_gaps)
        if not any(item.get("cinematographers") for item in history_items):
            gaps.append("Cinematographer metadata was not present in the supplied Plex items.")
        if not any(item.get("summary") for item in history_items):
            gaps.append("Some watched items were missing plot summaries in Plex metadata.")
        deduped: list[str] = []
        for gap in gaps:
            if gap not in deduped:
                deduped.append(gap)
        return deduped
