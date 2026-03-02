#!/usr/bin/env python3

from __future__ import annotations

import html
import random
from typing import Any, Callable


def render_movie_likeness_page(
    *,
    session_id: str,
    token: str,
    account_id: int,
    refresh: bool,
    saved: bool,
    movie_likeness_store: Any,
    plex_pms: Any,
    library_candidate_limit: int,
    render_body: Callable[..., str],
) -> str:
    movie_likeness_state = movie_likeness_store.get_state(session_id)
    saved_ratings = movie_likeness_state.get("ratings", {})
    movie_items = _ensure_movie_likeness_batch(
        session_id=session_id,
        token=token,
        account_id=account_id,
        refresh=refresh,
        movie_likeness_store=movie_likeness_store,
        plex_pms=plex_pms,
        library_candidate_limit=library_candidate_limit,
    )
    movie_markup = _render_movie_likeness_group(
        items=movie_items,
        saved_ratings=saved_ratings,
        render_body=render_body,
    )
    save_status = ""
    if saved:
        save_status = '<p class="notice">Saved likeness ratings for the current movie set.</p>'
    refreshed_state = movie_likeness_store.get_state(session_id)
    return render_body(
        "movie_likeness_page",
        movie_count=html.escape(str(len(movie_items))),
        rated_count=html.escape(str(len(saved_ratings))),
        generated_at=html.escape(str(refreshed_state.get("batch_generated_at") or "unknown")),
        save_status=save_status,
        movie_likeness_markup=movie_markup,
    )


def save_movie_likeness_ratings(
    *,
    session_id: str,
    token: str,
    account_id: int,
    form: dict[str, list[str]],
    movie_likeness_store: Any,
    plex_pms: Any,
    library_candidate_limit: int,
) -> None:
    movie_likeness_state = movie_likeness_store.get_state(session_id)
    movie_items = movie_likeness_state.get("batch")
    if not isinstance(movie_items, list) or not movie_items:
        movie_items = _ensure_movie_likeness_batch(
            session_id=session_id,
            token=token,
            account_id=account_id,
            refresh=True,
            movie_likeness_store=movie_likeness_store,
            plex_pms=plex_pms,
            library_candidate_limit=library_candidate_limit,
        )
    allowed_rating_keys = {
        str(item.get("rating_key") or "")
        for item in movie_items
        if isinstance(item, dict) and item.get("rating_key")
    }
    saved_ratings = movie_likeness_state.get("ratings", {})
    updated_ratings = dict(saved_ratings)
    for rating_key in allowed_rating_keys:
        form_key = f"rating_{rating_key}"
        submitted_value = form.get(form_key, [""])[0]
        if submitted_value in {"1", "2", "3", "4", "5"}:
            updated_ratings[rating_key] = int(submitted_value)
    movie_likeness_store.save_ratings(session_id, updated_ratings)
    movie_likeness_store.clear_batch(session_id)


def _ensure_movie_likeness_batch(
    *,
    session_id: str,
    token: str,
    account_id: int,
    refresh: bool,
    movie_likeness_store: Any,
    plex_pms: Any,
    library_candidate_limit: int,
) -> list[dict[str, Any]]:
    existing_batch = movie_likeness_store.get_state(session_id).get("batch")
    if not refresh and isinstance(existing_batch, list) and existing_batch:
        return [item for item in existing_batch if isinstance(item, dict)]

    saved_ratings = movie_likeness_store.get_state(session_id).get("ratings", {})
    rated_keys = {str(key) for key in saved_ratings.keys()}

    library_candidates = plex_pms.get_library_candidates(
        token, account_id, library_candidate_limit
    )
    available_movies = [
        candidate
        for candidate in library_candidates
        if isinstance(candidate, dict)
        and str(candidate.get("media_type") or "").casefold() == "movie"
        and str(candidate.get("rating_key") or "") not in rated_keys
    ]
    if len(available_movies) > 5:
        batch = random.sample(available_movies, 5)
    else:
        batch = available_movies
    movie_likeness_store.replace_batch(session_id, batch)
    return batch


def _render_movie_likeness_group(
    *,
    items: list[dict[str, Any]],
    saved_ratings: dict[str, Any],
    render_body: Callable[..., str],
) -> str:
    if not items:
        return render_body("recommendations_empty")
    cards: list[str] = []
    for item in items:
        rating_key = str(item.get("rating_key") or "")
        saved_rating = saved_ratings.get(rating_key)
        cards.append(
            render_body(
                "movie_likeness_item",
                title=html.escape(str(item.get("title") or "Unknown title")),
                year=html.escape(str(item.get("year") or "unknown")),
                why_it_matches=html.escape(
                    str(item.get("why_it_matches") or "No reason provided.")
                ),
                supporting_signals=html.escape(
                    ", ".join(item.get("supporting_signals", []))
                    if isinstance(item.get("supporting_signals"), list)
                    else ""
                ),
                rating_key=html.escape(rating_key),
                current_rating=html.escape(
                    str(saved_rating) if saved_rating in {1, 2, 3, 4, 5} else "Not rated"
                ),
                never_seen_checked="",
                rating_checked_1="checked" if saved_rating == 1 else "",
                rating_checked_2="checked" if saved_rating == 2 else "",
                rating_checked_3="checked" if saved_rating == 3 else "",
                rating_checked_4="checked" if saved_rating == 4 else "",
                rating_checked_5="checked" if saved_rating == 5 else "",
            )
        )
    return "".join(cards)
