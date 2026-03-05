#!/usr/bin/env python3

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

try:
    from .plex_file_io import write_text_locked
except ImportError:
    from plex_file_io import write_text_locked


class RecommendationError(RuntimeError):
    pass


class ExistingContentRecommendation(BaseModel):
    title: str = Field(description="Exact Plex title already available on the local Plex server.")
    media_type: str = Field(description="Plex media type such as movie or show.")
    year: int | None = Field(description="Release year if known.")
    why_it_matches: str = Field(
        description="Short explanation of why this item fits the user's recent viewing summary."
    )
    supporting_signals: list[str] = Field(
        description="Compact supporting signals such as actor overlap, shared genres, or plot context."
    )


class ExternalContentRecommendation(BaseModel):
    title: str = Field(description="Title of recommended content not currently available on the Plex server.")
    media_type: str = Field(description="Likely media type such as movie or show.")
    year: int | None = Field(description="Release year if known.")
    why_it_matches: str = Field(
        description="Short explanation of why this item fits the user's recent viewing summary."
    )
    supporting_signals: list[str] = Field(
        description="Compact supporting signals such as actor overlap, shared genres, or plot context."
    )
    lookup_hint: str = Field(
        description="A concise lookup hint that could be used later for acquisition or catalog matching."
    )


class RecommendationNarrative(BaseModel):
    executive_summary: str = Field(
        description="A concise explanation of the recommendation strategy for this user."
    )
    on_server_recommendations: list[ExistingContentRecommendation] = Field(
        description="Recommended titles that already exist on the Plex server."
    )
    off_server_recommendations: list[ExternalContentRecommendation] = Field(
        description="Recommended titles that do not currently exist on the Plex server."
    )
    source_gaps: list[str] = Field(
        description="Data caveats, such as limited library candidate coverage or missing cinematographer credits."
    )


class OnServerRecommendationNarrative(BaseModel):
    on_server_recommendations: list[ExistingContentRecommendation] = Field(
        description="Recommended titles that already exist on the Plex server."
    )
    source_gaps: list[str] = Field(
        description="Data caveats specific to the on-server recommendation pass."
    )


class OffServerRecommendationNarrative(BaseModel):
    executive_summary: str = Field(
        description="A concise explanation of the recommendation strategy for this user."
    )
    off_server_recommendations: list[ExternalContentRecommendation] = Field(
        description="Recommended titles that do not currently exist on the Plex server."
    )
    source_gaps: list[str] = Field(
        description="Data caveats specific to the off-server recommendation pass."
    )


class PlexRecommendationService:
    def __init__(self) -> None:
        self._agents_src = (
            Path(__file__).resolve().parent.parent / "openai-agents-python" / "src"
        )
        self._library_candidates_dump_path = (
            Path(__file__).resolve().parent / "debug_output" / "recommendation_candidates_submission.json"
        )
        self._token_estimate_dump_path = (
            Path(__file__).resolve().parent / "debug_output" / "recommendation_token_estimate.json"
        )
        prompt_dump_dir = Path(__file__).resolve().parent / "debug_output"
        self._prompt_dump_paths = {
            "on_server": prompt_dump_dir / "recommendation_on_server_prompt_submission.json",
            "off_server": prompt_dump_dir / "recommendation_off_server_prompt_submission.json",
        }
        self._final_output_dump_path = (
            Path(__file__).resolve().parent / "debug_output" / "recommendation_final_output.json"
        )

    def recommend(
        self,
        *,
        account_id: int,
        viewing_summary: dict[str, Any],
        movie_likeness_commonality: dict[str, Any] | None,
        library_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        if not viewing_summary:
            raise RecommendationError("Viewing summary is required before generating recommendations.")

        filtered_candidates = self._exclude_recently_watched(
            library_candidates, viewing_summary.get("watched_items", [])
        )
        narrative = self._generate_recommendations(
            viewing_summary,
            movie_likeness_commonality,
            library_candidates,
            filtered_candidates,
        )
        on_server_recommendations = self._normalize_on_server_recommendations(
            narrative.on_server_recommendations, filtered_candidates
        )
        off_server_recommendations = self._normalize_off_server_recommendations(
            narrative.off_server_recommendations, library_candidates
        )
        return {
            "schema_version": "plex_recommendations.v1",
            "generated_at": generated_at,
            "account_id": account_id,
            "based_on_summary_version": viewing_summary.get("schema_version"),
            "executive_summary": narrative.executive_summary,
            "on_server_recommendations": on_server_recommendations,
            "off_server_recommendations": off_server_recommendations,
            "source_gaps": self._combine_gaps(
                narrative.source_gaps,
                candidate_count=len(filtered_candidates),
                original_candidate_count=len(library_candidates),
                on_server_count=len(on_server_recommendations),
            ),
        }

    def _generate_recommendations(
        self,
        viewing_summary: dict[str, Any],
        movie_likeness_commonality: dict[str, Any] | None,
        library_candidates: list[dict[str, Any]],
        filtered_candidates: list[dict[str, Any]],
    ) -> RecommendationNarrative:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RecommendationError(
                "OPENAI_API_KEY is not set. Set it before opening /recommendations."
            )
        Agent, Runner, set_tracing_disabled = self._load_agents_sdk()
        set_tracing_disabled(True)
        model_name = os.environ.get("OPENAI_AGENT_MODEL", "gpt-5-nano")
        on_server_agent = Agent(
            name="PlexOnServerRecommendations",
            model=model_name,
            instructions=(
                "You produce structured on-server entertainment recommendations from a viewing summary. "
                "Use only the supplied viewing summary, supplied movie_likeness_commonality, "
                "and supplied filtered_candidates. "
                "The movie_likeness_commonality comes from direct user ratings and should be treated "
                "as slightly more informative than inferred watch-history patterns when the signals conflict. "
                "The filtered_candidates payload uses a fields legend with row arrays in that exact order. "
                "Choose 5 to 10 on-server recommendations only from the provided filtered_candidates list, "
                "with movies representing 75% of recommendations and shows 25%, "
                "preserving exact title, media_type, and year values. "
                "Ensure recommendations are distinct from each other and should not appear in watched_items. "
                "Return only on-server recommendations and source_gaps."
            ),
            output_type=OnServerRecommendationNarrative,
        )
        off_server_agent = Agent(
            name="PlexOffServerRecommendations",
            model=model_name,
            instructions=(
                "You produce structured off-server entertainment recommendations from a viewing summary. "
                "Use only the supplied viewing summary, supplied movie_likeness_commonality, "
                "and supplied Plex library candidates. "
                "The movie_likeness_commonality comes from direct user ratings and should be treated "
                "as slightly more informative than inferred watch-history patterns when the signals conflict. "
                "The library_candidates payload uses a fields legend with row arrays in that exact order. "
                "Choose 5 to 10 off-server recommendations that do not appear in the provided library_candidates. "
                "Use plot_context_observations, executive_summary, viewer_profile_tags, actors, directors, "
                "cinematographers, and genres where available. Keep reasons compact and database-friendly. "
                "Return an executive_summary, off_server_recommendations, and source_gaps."
            ),
            output_type=OffServerRecommendationNarrative,
        )
        prompt_library_candidates = self._serialize_library_candidates_for_prompt(
            library_candidates
        )
        prompt_filtered_candidates = self._serialize_library_candidates_for_prompt(
            filtered_candidates
        )
        prompt_viewing_summary = self._serialize_viewing_summary_for_prompt(viewing_summary)
        prompt_movie_likeness_commonality = self._serialize_movie_likeness_commonality_for_prompt(
            movie_likeness_commonality
        )
        self._write_library_candidates_dump(prompt_library_candidates)
        on_server_prompt = json.dumps(
            {
                "task": "Generate only recommendations that already exist on Plex.",
                "viewing_summary": prompt_viewing_summary,
                "movie_likeness_commonality": prompt_movie_likeness_commonality,
                "filtered_candidates": prompt_filtered_candidates,
            },
            separators=(",", ":"),
        )
        off_server_prompt = json.dumps(
            {
                "task": "Generate only recommendations that do not currently exist on Plex.",
                "viewing_summary": prompt_viewing_summary,
                "movie_likeness_commonality": prompt_movie_likeness_commonality,
                "library_candidates": prompt_library_candidates,
            },
            separators=(",", ":"),
        )
        estimated_token_usage = {
            "on_server": self._build_estimated_token_usage(
                model_name=model_name,
                instructions=on_server_agent.instructions,
                prompt=on_server_prompt,
                output_type=OnServerRecommendationNarrative,
            ),
            "off_server": self._build_estimated_token_usage(
                model_name=model_name,
                instructions=off_server_agent.instructions,
                prompt=off_server_prompt,
                output_type=OffServerRecommendationNarrative,
            ),
        }
        self._write_token_estimate_dump(estimated_token_usage)
        self._write_prompt_dump("on_server", on_server_prompt)
        self._write_prompt_dump("off_server", off_server_prompt)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                on_server_future = executor.submit(
                    self._run_recommendation_call,
                    Runner=Runner,
                    agent=on_server_agent,
                    prompt=on_server_prompt,
                    call_name="on_server",
                )
                off_server_future = executor.submit(
                    self._run_recommendation_call,
                    Runner=Runner,
                    agent=off_server_agent,
                    prompt=off_server_prompt,
                    call_name="off_server",
                )
                on_server_result = on_server_future.result()
                off_server_result = off_server_future.result()
        except Exception as exc:  # pragma: no cover - runtime integration path
            if isinstance(exc, RecommendationError):
                raise exc
            raise RecommendationError(
                f"OpenAI recommendation generation failed: {exc}"
            ) from exc
        narrative = RecommendationNarrative(
            executive_summary=off_server_result.executive_summary,
            on_server_recommendations=on_server_result.on_server_recommendations,
            off_server_recommendations=off_server_result.off_server_recommendations,
            source_gaps=self._dedupe_strings(
                [*on_server_result.source_gaps, *off_server_result.source_gaps]
            ),
        )
        self._write_final_output_dump(narrative)
        return narrative

    def _build_estimated_token_usage(
        self,
        *,
        model_name: str,
        instructions: str | None,
        prompt: str,
        output_type: type[BaseModel],
    ) -> dict[str, Any]:
        instructions_text = instructions if isinstance(instructions, str) else str(instructions or "")
        output_schema = json.dumps(output_type.model_json_schema(), indent=2)
        instructions_estimate = self._estimate_tokens(instructions_text, model_name)
        prompt_estimate = self._estimate_tokens(prompt, model_name)
        schema_estimate = self._estimate_tokens(output_schema, model_name)
        return {
            "model": model_name,
            "method": instructions_estimate["method"],
            "notes": instructions_estimate.get("notes"),
            "input_tokens": {
                "instructions": instructions_estimate["tokens"],
                "prompt": prompt_estimate["tokens"],
                "output_schema": schema_estimate["tokens"],
                "total": (
                    instructions_estimate["tokens"]
                    + prompt_estimate["tokens"]
                    + schema_estimate["tokens"]
                ),
            },
        }

    def _estimate_tokens(self, text: str, model_name: str) -> dict[str, Any]:
        if not text:
            return {"tokens": 0, "method": "empty", "notes": None}
        try:
            import tiktoken
        except ImportError:
            return {
                "tokens": max(1, len(text) // 4),
                "method": "heuristic_chars_div_4",
                "notes": "tiktoken is not installed; token estimate is approximate.",
            }
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return {
            "tokens": len(encoding.encode(text)),
            "method": "tiktoken",
            "notes": None,
        }

    def _write_token_estimate_dump(self, payload: dict[str, Any]) -> None:
        write_text_locked(
            self._token_estimate_dump_path,
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _write_prompt_dump(self, prompt_name: str, prompt: str) -> None:
        prompt_dump_path = self._prompt_dump_paths.get(prompt_name)
        if prompt_dump_path is None:
            raise RecommendationError(f"Unknown prompt dump target: {prompt_name}")
        write_text_locked(prompt_dump_path, prompt, encoding="utf-8")

    def _write_final_output_dump(self, final_output: Any) -> None:
        model_dump_json = getattr(final_output, "model_dump_json", None)
        if callable(model_dump_json):
            output_text = str(model_dump_json(indent=2))
        else:
            output_text = str(final_output)
        write_text_locked(self._final_output_dump_path, output_text, encoding="utf-8")

    def _load_agents_sdk(self) -> tuple[Any, Any, Any]:
        if not self._agents_src.exists():
            raise RecommendationError(
                f"Agents SDK source directory was not found at {self._agents_src}."
            )
        agents_src = str(self._agents_src)
        if agents_src not in sys.path:
            sys.path.insert(0, agents_src)
        try:
            from agents import Agent, Runner, set_tracing_disabled
        except Exception as exc:  # pragma: no cover - environment-specific import path
            raise RecommendationError(
                f"Could not import the local openai-agents-python SDK: {exc}"
            ) from exc
        return Agent, Runner, set_tracing_disabled

    def _run_recommendation_call(
        self, *, Runner: Any, agent: Any, prompt: str, call_name: str
    ) -> Any:
        try:
            result = Runner.run_sync(agent, prompt)
        except Exception as exc:  # pragma: no cover - runtime integration path
            raise RecommendationError(
                f"OpenAI {call_name} recommendation generation failed: {exc}"
            ) from exc
        return result.final_output

    def _serialize_library_candidates_for_prompt(
        self, library_candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "fields": ["title", "media_type", "year"],
            "items": [
                [
                    candidate.get("title"),
                    candidate.get("media_type"),
                    candidate.get("year"),
                ]
                for candidate in library_candidates
            ],
        }

    def _serialize_viewing_summary_for_prompt(
        self, viewing_summary: dict[str, Any]
    ) -> dict[str, Any]:
        prompt_viewing_summary = dict(viewing_summary)
        prompt_viewing_summary["watched_items"] = self._serialize_library_candidates_for_prompt(
            viewing_summary.get("watched_items", [])
        )
        return prompt_viewing_summary

    def _serialize_movie_likeness_commonality_for_prompt(
        self, movie_likeness_commonality: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not isinstance(movie_likeness_commonality, dict):
            return {
                "commonality_summary": "",
                "updated_at": None,
                "source_gaps": ["No direct movie likeness commonality summary was available."],
            }
        return {
            "commonality_summary": movie_likeness_commonality.get("commonality_summary") or "",
            "updated_at": movie_likeness_commonality.get("commonality_updated_at"),
            "source_gaps": movie_likeness_commonality.get("commonality_source_gaps", []),
        }

    def _write_library_candidates_dump(
        self, library_candidates: dict[str, Any]
    ) -> None:
        write_text_locked(
            self._library_candidates_dump_path,
            json.dumps(library_candidates, indent=2),
            encoding="utf-8",
        )

    def _exclude_recently_watched(
        self, library_candidates: list[dict[str, Any]], watched_items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        watched_titles = {
            str(item.get("title")).casefold()
            for item in watched_items
            if isinstance(item, dict) and item.get("title")
        }
        return [
            candidate
            for candidate in library_candidates
            if str(candidate.get("title") or "").casefold() not in watched_titles
        ]

    def _combine_gaps(
        self,
        source_gaps: list[str],
        *,
        candidate_count: int,
        original_candidate_count: int,
        on_server_count: int,
    ) -> list[str]:
        gaps = list(source_gaps)
        gaps.append(
            f"Recommendation pass considered {candidate_count} Plex library candidates after filtering recently watched items."
        )
        if candidate_count < original_candidate_count:
            gaps.append("Some on-server titles were excluded because they were already in recent viewing history.")
        if on_server_count == 0:
            gaps.append("No model-selected recommendations could be matched back to confirmed Plex library candidates.")
        deduped: list[str] = []
        for gap in gaps:
            if gap not in deduped:
                deduped.append(gap)
        return deduped

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    def _normalize_on_server_recommendations(
        self,
        recommendations: list[ExistingContentRecommendation],
        library_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates_by_lookup = {
            self._candidate_lookup_key(candidate): candidate
            for candidate in library_candidates
            if self._candidate_lookup_key(candidate) is not None
        }
        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for recommendation in recommendations:
            lookup_key = self._recommendation_lookup_key(recommendation)
            if lookup_key is None:
                continue
            candidate = candidates_by_lookup.get(lookup_key)
            if candidate is None:
                continue
            rating_key = candidate.get("rating_key")
            if rating_key is None:
                continue
            rating_key_str = str(rating_key)
            if rating_key_str in seen_keys:
                continue
            seen_keys.add(rating_key_str)
            normalized.append(
                {
                    "title": candidate.get("title"),
                    "media_type": candidate.get("media_type"),
                    "year": candidate.get("year"),
                    "plex_rating_key": rating_key_str,
                    "library_section_title": candidate.get("library_section_title"),
                    "rotten_tomatoes_url": self._build_rotten_tomatoes_search_url(
                        candidate.get("title"), candidate.get("year")
                    ),
                    "rotten_tomatoes_critic_score": self._extract_rotten_tomatoes_score(
                        candidate.get("rating"), candidate.get("rating_image")
                    ),
                    "rotten_tomatoes_audience_score": self._extract_rotten_tomatoes_score(
                        candidate.get("audience_rating"),
                        candidate.get("audience_rating_image"),
                    ),
                    "why_it_matches": recommendation.why_it_matches,
                    "supporting_signals": recommendation.supporting_signals,
                }
            )
        return normalized

    def _normalize_off_server_recommendations(
        self,
        recommendations: list[ExternalContentRecommendation],
        library_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidate_titles = {
            str(candidate.get("title") or "").casefold() for candidate in library_candidates
        }
        normalized: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for recommendation in recommendations:
            title_key = recommendation.title.casefold()
            if title_key in candidate_titles or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            normalized_item = recommendation.model_dump()
            normalized_item["rotten_tomatoes_url"] = self._build_rotten_tomatoes_search_url(
                recommendation.title, recommendation.year
            )
            normalized_item["rotten_tomatoes_critic_score"] = None
            normalized_item["rotten_tomatoes_audience_score"] = None
            normalized.append(normalized_item)
        return normalized

    def _extract_rotten_tomatoes_score(
        self, rating_value: Any, rating_image: Any
    ) -> int | None:
        if not isinstance(rating_image, str):
            return None
        if not rating_image.startswith("rottentomatoes://"):
            return None
        if not isinstance(rating_value, (int, float)):
            return None
        return max(0, min(100, int(round(float(rating_value) * 10))))

    def _build_rotten_tomatoes_search_url(self, title: Any, year: Any) -> str:
        parts: list[str] = []
        if isinstance(title, str) and title:
            parts.append(title)
        if year not in (None, ""):
            parts.append(str(year))
        query = " ".join(parts).strip()
        if not query:
            return "https://www.rottentomatoes.com/search?search="
        return f"https://www.rottentomatoes.com/search?search={quote_plus(query)}"

    def _candidate_lookup_key(self, candidate: dict[str, Any]) -> tuple[str, str, str] | None:
        title = candidate.get("title")
        media_type = candidate.get("media_type")
        year = candidate.get("year")
        if not isinstance(title, str) or not title:
            return None
        return (
            title.casefold(),
            str(media_type or "").casefold(),
            str(year or ""),
        )

    def _recommendation_lookup_key(
        self, recommendation: ExistingContentRecommendation
    ) -> tuple[str, str, str] | None:
        if not recommendation.title:
            return None
        return (
            recommendation.title.casefold(),
            str(recommendation.media_type or "").casefold(),
            str(recommendation.year or ""),
        )
