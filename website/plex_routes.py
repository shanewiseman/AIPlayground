#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

try:
    from .plex_api import PlexApiError
    from .plex_history_summary import ViewingSummaryError
    from .plex_movie_likeness_commonality import MovieLikenessCommonalityError
    from .plex_movie_likeness import (
        render_movie_likeness_page,
        save_movie_likeness_ratings,
    )
    from .plex_recommendations import RecommendationError
except ImportError:
    from plex_api import PlexApiError
    from plex_history_summary import ViewingSummaryError
    from plex_movie_likeness_commonality import MovieLikenessCommonalityError
    from plex_movie_likeness import (
        render_movie_likeness_page,
        save_movie_likeness_ratings,
    )
    from plex_recommendations import RecommendationError


SESSION_COOKIE = "plex_auth_session"


def load_route_templates(template_dir: Path) -> dict[str, str]:
    templates: dict[str, str] = {}
    for template_path in sorted(template_dir.glob("*.html")):
        templates[template_path.stem] = template_path.read_text(encoding="utf-8")
    return templates


def render_page(title: str, body: str) -> bytes:
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --panel: rgba(255, 252, 246, 0.94);
      --text: #1d1b16;
      --muted: #6f6658;
      --accent: #d96c0f;
      --accent-dark: #9d4300;
      --border: #dfd2bc;
      --shadow: 0 20px 40px rgba(56, 41, 24, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(217, 108, 15, 0.15), transparent 30%),
        radial-gradient(circle at right, rgba(47, 126, 106, 0.16), transparent 28%),
        linear-gradient(160deg, #f3ecdc 0%, #efe4cc 100%);
      min-height: 100vh;
    }}
    main {{
      width: min(920px, calc(100% - 32px));
      margin: 40px auto;
      padding: 28px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(6px);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(2rem, 4vw, 3.6rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 1.2rem;
    }}
    p {{
      margin: 12px 0;
      line-height: 1.6;
    }}
    .lede {{
      max-width: 56ch;
      color: var(--muted);
      font-size: 1.06rem;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 24px 0;
    }}
    .button {{
      display: inline-block;
      padding: 12px 18px;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      text-decoration: none;
      font-weight: 700;
      border: none;
    }}
    .button.secondary {{
      background: transparent;
      color: var(--accent-dark);
      border: 1px solid var(--border);
    }}
    .panel {{
      margin-top: 20px;
      padding: 18px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.72);
    }}
    .notice {{
      margin: 12px 0 0;
      padding: 12px 14px;
      border: 1px solid #bdd8cb;
      border-radius: 14px;
      background: #e8f5ef;
      color: #235a49;
      font-weight: 700;
    }}
    .history-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
      margin-top: 20px;
    }}
    .history-card {{
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.82);
    }}
    .history-art {{
      display: block;
      width: 100%;
      height: 160px;
      object-fit: cover;
      background: linear-gradient(135deg, #dcc4ab, #f2e7d6);
    }}
    .history-meta {{
      padding: 16px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .history-title {{
      margin: 8px 0 6px;
      font-size: 1.2rem;
      line-height: 1.1;
    }}
    .history-subtitle {{
      margin: 0 0 8px;
      color: var(--muted);
    }}
    .rating-form {{
      display: flex;
      flex-direction: column;
      gap: 20px;
    }}
    .rating-scale {{
      margin: 14px 0 0;
      padding: 0;
      border: 0;
    }}
    .rating-scale legend {{
      margin-bottom: 10px;
      font-weight: 700;
    }}
    .rating-options {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .rating-option {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.9);
    }}
    pre {{
      margin: 0;
      overflow-x: auto;
      padding: 16px;
      border-radius: 14px;
      background: #20170f;
      color: #f8f4ec;
      font-size: 0.94rem;
    }}
    code {{
      font-family: "Courier New", monospace;
    }}
    .status {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #efe0cf;
      color: #7d3910;
      font-size: 0.85rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .status.ok {{
      background: #dcefe8;
      color: #235a49;
    }}
    .error {{
      color: #8d2100;
      font-weight: 700;
    }}
    .loading-overlay {{
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(243, 236, 220, 0.82);
      backdrop-filter: blur(4px);
      z-index: 9999;
    }}
    .loading-overlay.visible {{
      display: flex;
    }}
    .loading-card {{
      min-width: 220px;
      padding: 20px 24px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: rgba(255, 252, 246, 0.98);
      box-shadow: var(--shadow);
      text-align: center;
    }}
    .loading-spinner {{
      width: 36px;
      height: 36px;
      margin: 0 auto 12px;
      border: 4px solid #eadbc5;
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.9s linear infinite;
    }}
    .loading-label {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    @keyframes spin {{
      to {{
        transform: rotate(360deg);
      }}
    }}
  </style>
</head>
<body>
  <div id="loading-overlay" class="loading-overlay" aria-hidden="true">
    <div class="loading-card" role="status" aria-live="polite">
      <div class="loading-spinner"></div>
      <p class="loading-label">Working on your request...</p>
    </div>
  </div>
  <main>
    {body}
  </main>
  <script>
    (() => {{
      const overlay = document.getElementById("loading-overlay");
      if (!overlay) return;
      const showLoading = () => {{
        overlay.classList.add("visible");
        overlay.setAttribute("aria-hidden", "false");
      }};
      document.querySelectorAll("a[data-loading='1']").forEach((link) => {{
        link.addEventListener("click", (event) => {{
          if (event.defaultPrevented) return;
          if (event.button !== 0) return;
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          showLoading();
        }});
      }});
      document.querySelectorAll("form[data-loading='1']").forEach((form) => {{
        form.addEventListener("submit", (event) => {{
          if (event.defaultPrevented) return;
          showLoading();
        }});
      }});
    }})();
  </script>
</body>
</html>"""
    return page.encode("utf-8")


class PlexAuthHandler(BaseHTTPRequestHandler):
    server_version = "PlexAuthDemo/0.1"

    @property
    def app(self) -> Any:
        return self.server

    def render_body(self, template_name: str, **context: str) -> str:
        return self.app.route_templates[template_name].format(**context)

    def do_GET(self) -> None:
        self.app.sessions.cleanup()
        self.app.movie_likeness_store.cleanup()
        parsed = urlparse(self.path)
        routes = {
            "/": self.handle_home,
            "/login": self.handle_login_page,
            "/login/start": self.handle_login_start,
            "/auth/plex/callback": self.handle_callback,
            "/logout": self.handle_logout,
            "/account": self.handle_account_page,
            "/history": self.handle_history_page,
            "/summary": self.handle_summary_page,
            "/recommendations": self.handle_recommendations_page,
            "/movie-likeness": self.handle_movie_likeness_page,
            "/artwork": self.handle_artwork,
            "/token.json": self.handle_token_json,
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self.respond_html(
                HTTPStatus.NOT_FOUND,
                "Not found",
                self.render_body("not_found"),
            )
            return
        try:
            handler(parsed)
        except PlexApiError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Plex error",
                self.render_body("plex_error", error_message=html.escape(str(exc))),
            )
        except ViewingSummaryError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Summary error",
                self.render_body("summary_error", error_message=html.escape(str(exc))),
            )
        except MovieLikenessCommonalityError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Movie likeness error",
                self.render_body(
                    "recommendations_error", error_message=html.escape(str(exc))
                ),
            )
        except RecommendationError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Recommendation error",
                self.render_body(
                    "recommendations_error", error_message=html.escape(str(exc))
                ),
            )

    def do_POST(self) -> None:
        self.app.sessions.cleanup()
        self.app.movie_likeness_store.cleanup()
        parsed = urlparse(self.path)
        routes = {
            "/movie-likeness": self.handle_movie_likeness_submit,
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self.respond_html(
                HTTPStatus.NOT_FOUND,
                "Not found",
                self.render_body("not_found"),
            )
            return
        try:
            handler(parsed)
        except PlexApiError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Plex error",
                self.render_body("plex_error", error_message=html.escape(str(exc))),
            )
        except ViewingSummaryError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Summary error",
                self.render_body("summary_error", error_message=html.escape(str(exc))),
            )
        except MovieLikenessCommonalityError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Movie likeness error",
                self.render_body(
                    "recommendations_error", error_message=html.escape(str(exc))
                ),
            )
        except RecommendationError as exc:
            self.respond_html(
                HTTPStatus.BAD_GATEWAY,
                "Recommendation error",
                self.render_body(
                    "recommendations_error", error_message=html.escape(str(exc))
                ),
            )

    def handle_home(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        username = session.get("plex_username")
        account_id = session.get("plex_account_id")
        if token:
            body = self.render_body(
                "home_connected",
                username=html.escape(username or "unknown"),
                account_id=html.escape(str(account_id or "unknown")),
                token=html.escape(token),
            )
        else:
            body = self.render_body(
                "home_signed_out",
                client_identifier=html.escape(self.app.config.client_identifier),
                callback_url=html.escape(self.app.config.callback_url),
            )
        self.respond_html(HTTPStatus.OK, "Plex Token Gateway", body)

    def handle_login_page(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        username = session.get("plex_username")
        account_id = session.get("plex_account_id")
        if token:
            body = self.render_body(
                "login_connected",
                username=html.escape(username or "unknown"),
                account_id=html.escape(str(account_id or "unknown")),
            )
        else:
            body = self.render_body("login_signed_out")
        self.respond_html(HTTPStatus.OK, "Login With Plex", body)

    def handle_login_start(self, parsed: Any) -> None:
        session_id, session = self.get_or_create_session()
        pin = self.app.plex_tv.create_pin()
        session["pin_id"] = pin["id"]
        session["pin_code"] = pin["code"]
        session["created_at"] = time.time()
        self.respond_redirect(self.app.plex_tv.build_login_url(pin["code"]), session_id)

    def handle_callback(self, parsed: Any) -> None:
        session_id, session = self.get_or_create_session()
        pin_id = session.get("pin_id")
        if not pin_id:
            self.respond_html(
                HTTPStatus.BAD_REQUEST,
                "Missing PIN",
                self.render_body("missing_pin"),
            )
            return

        token = None
        for _ in range(self.app.config.pin_poll_attempts):
            pin = self.app.plex_tv.get_pin(pin_id)
            token = pin.get("authToken")
            if token:
                break
            time.sleep(self.app.config.pin_poll_interval_seconds)

        if not token:
            self.respond_html(
                HTTPStatus.ACCEPTED,
                "Waiting for Plex",
                self.render_body("waiting_for_plex"),
            )
            return

        user_profile = self.app.plex_tv.get_user_profile(token)
        session["plex_token"] = token
        session["plex_username"] = user_profile.get("username") or "Authenticated Plex user"
        session["plex_account_id"] = user_profile.get("account_id")
        session.pop("pin_id", None)
        session.pop("pin_code", None)
        session.pop("history_summary", None)
        session.pop("recommendations", None)
        self.app.movie_likeness_store.delete(session_id)
        self.respond_redirect("/login", session_id)

    def handle_logout(self, parsed: Any) -> None:
        session_id, session = self.get_or_create_session()
        session.pop("plex_token", None)
        session.pop("plex_username", None)
        session.pop("plex_account_id", None)
        session.pop("history_summary", None)
        session.pop("recommendations", None)
        session.pop("pin_id", None)
        session.pop("pin_code", None)
        self.app.movie_likeness_store.delete(session_id)
        self.respond_redirect("/", session_id)

    def handle_account_page(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_account"),
            )
            return
        body = self.render_body(
            "account_page",
            account_id=html.escape(str(account_id or "unknown")),
        )
        self.respond_html(HTTPStatus.OK, "Plex Account Id", body)

    def handle_history_page(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token or not account_id:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_history"),
            )
            return

        history_items = self.app.plex_pms.get_enriched_history(
            token, int(account_id), self.app.config.history_item_limit
        )
        cards: list[str] = []
        for item in history_items:
            title = item.get("title") or "Unknown title"
            subtitle = " / ".join(
                part
                for part in [item.get("series_title"), item.get("season_title")]
                if part
            )
            viewed_at = item.get("viewed_at")
            viewed_label = "Unknown watch time"
            if isinstance(viewed_at, int):
                viewed_label = datetime.fromtimestamp(viewed_at).strftime("%Y-%m-%d %H:%M")

            art_path = item.get("art_path") or item.get("thumb_path")
            if isinstance(art_path, str) and art_path.startswith("/"):
                artwork_url = f"/artwork?path={quote(art_path, safe='')}"
                artwork_html = (
                    f'<img class="history-art" src="{artwork_url}" '
                    f'alt="{html.escape(str(title))} artwork">'
                )
            else:
                artwork_html = '<div class="history-art"></div>'

            cards.append(
                self.render_body(
                    "history_card",
                    artwork_html=artwork_html,
                    media_type=html.escape(str(item.get("media_type") or "media")),
                    title=html.escape(str(title)),
                    subtitle=html.escape(subtitle or "No series context"),
                    viewed_label=html.escape(viewed_label),
                    rating_key=html.escape(str(item.get("rating_key") or "unknown")),
                )
            )

        history_markup = "".join(cards) if cards else (
            self.render_body("history_empty")
        )
        body = self.render_body(
            "history_page",
            pms_base_url=html.escape(self.app.config.pms_base_url),
            account_id=html.escape(str(account_id)),
            history_markup=history_markup if not cards else f'<section class="history-grid">{history_markup}</section>',
        )
        self.respond_html(HTTPStatus.OK, "Last Watched Items", body)

    def handle_summary_page(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token or not account_id:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_summary"),
            )
            return

        query = parse_qs(parsed.query)
        refresh = query.get("refresh", ["0"])[0] == "1"
        summary_object = session.get("history_summary")
        if refresh or not isinstance(summary_object, dict):
            history_items = self.app.plex_pms.get_enriched_history(
                token, int(account_id), self.app.config.history_item_limit
            )
            summary_object = self.app.history_summary_service.summarize(
                account_id=int(account_id),
                history_items=history_items,
            )
            session["history_summary"] = summary_object

        summary_json = html.escape(json.dumps(summary_object, indent=2))
        body = self.render_body(
            "summary_page",
            executive_summary=html.escape(str(summary_object.get("executive_summary", ""))),
            item_count=html.escape(str(summary_object.get("item_count", 0))),
            generated_at=html.escape(str(summary_object.get("generated_at", "unknown"))),
            summary_json=summary_json,
        )
        self.respond_html(HTTPStatus.OK, "Viewing Summary", body)

    def handle_movie_likeness_page(self, parsed: Any) -> None:
        session_id, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token or not account_id:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_recommendations"),
            )
            return

        query = parse_qs(parsed.query)
        refresh = query.get("refresh", ["0"])[0] == "1"
        saved = query.get("saved", ["0"])[0] == "1"
        body = render_movie_likeness_page(
            session_id=session_id,
            token=token,
            account_id=int(account_id),
            refresh=refresh,
            saved=saved,
            movie_likeness_store=self.app.movie_likeness_store,
            plex_pms=self.app.plex_pms,
            library_candidate_limit=self.app.config.library_candidate_limit,
            render_body=self.render_body,
        )
        self.respond_html(HTTPStatus.OK, "Movie Likeness", body)

    def handle_movie_likeness_submit(self, parsed: Any) -> None:
        session_id, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token or not account_id:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_recommendations"),
            )
            return

        form = self._parse_form_body()
        save_movie_likeness_ratings(
            session_id=session_id,
            token=token,
            account_id=int(account_id),
            form=form,
            movie_likeness_store=self.app.movie_likeness_store,
            movie_likeness_commonality_service=self.app.movie_likeness_commonality_service,
            plex_pms=self.app.plex_pms,
            library_candidate_limit=self.app.config.library_candidate_limit,
        )
        session.pop("recommendations", None)
        self.respond_redirect("/movie-likeness?saved=1", session_id)

    def handle_recommendations_page(self, parsed: Any) -> None:
        """
        Handle the recommendations page request by retrieving and displaying personalized recommendations.

        This method fetches the user's Plex viewing history and library candidates to generate
        personalized content recommendations. The results are cached in the session and can be
        refreshed on demand.

        Args:
            parsed: A parsed request object containing query parameters.

        Returns:
            None. Responds with HTML content containing:
                - An executive summary of recommendations
                - On-server recommendations (available in user's Plex library)
                - Off-server recommendations (not in user's library)
                - End-to-end recommendation retrieval duration for this HTTP request
                - JSON representation of the full recommendation object

        Raises:
            Responds with HTTPStatus.UNAUTHORIZED if user is not logged in (missing token or account_id).

        Query Parameters:
            refresh (optional): If set to "1", forces regeneration of recommendations and summary.
                               Otherwise uses cached session data if available.

        Side Effects:
            - Updates session with "history_summary" and "recommendations" data
            - Calls Plex PMS API to fetch enriched history and library candidates
            - Invokes history summarization and recommendation services
        """
        request_started_at = time.perf_counter()
        session_id, session = self.get_or_create_session()
        token = session.get("plex_token")
        account_id = session.get("plex_account_id")
        if not token or not account_id:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_recommendations"),
            )
            return

        query = parse_qs(parsed.query)
        refresh = query.get("refresh", ["0"])[0] == "1"
        recommendation_object = self._ensure_recommendation_object(
            session_id, session, token, int(account_id), refresh=refresh
        )
        recommendation_retrieval_duration = self._format_elapsed_duration(
            time.perf_counter() - request_started_at
        )

        on_server_markup = self._render_recommendation_group(
            recommendation_object.get("on_server_recommendations", []),
            include_plex_fields=True,
        )
        off_server_markup = self._render_recommendation_group(
            recommendation_object.get("off_server_recommendations", []),
            include_plex_fields=False,
        )
        recommendation_json = html.escape(json.dumps(recommendation_object, indent=2))
        body = self.render_body(
            "recommendations_page",
            executive_summary=html.escape(
                str(recommendation_object.get("executive_summary", ""))
            ),
            generated_at=html.escape(
                str(recommendation_object.get("generated_at", "unknown"))
            ),
            recommendation_retrieval_duration=html.escape(
                recommendation_retrieval_duration
            ),
            on_server_markup=on_server_markup,
            off_server_markup=off_server_markup,
            recommendation_json=recommendation_json,
        )
        self.respond_html(HTTPStatus.OK, "Recommendations", body)

    def _ensure_recommendation_object(
        self,
        session_id: str,
        session: dict[str, Any],
        token: str,
        account_id: int,
        *,
        refresh: bool,
    ) -> dict[str, Any]:
        summary_object = session.get("history_summary")
        if refresh or not isinstance(summary_object, dict):
            history_items = self.app.plex_pms.get_enriched_history(
                token, account_id, self.app.config.history_item_limit
            )
            summary_object = self.app.history_summary_service.summarize(
                account_id=account_id,
                history_items=history_items,
            )
            session["history_summary"] = summary_object

        recommendation_object = session.get("recommendations")
        if refresh or not isinstance(recommendation_object, dict):
            library_candidates = self.app.plex_pms.get_library_candidates(
                token, account_id, self.app.config.library_candidate_limit
            )
            movie_likeness_commonality = self.app.movie_likeness_store.get_state(session_id)
            recommendation_object = self.app.recommendation_service.recommend(
                account_id=account_id,
                viewing_summary=summary_object,
                movie_likeness_commonality=movie_likeness_commonality,
                library_candidates=library_candidates,
            )
            session["recommendations"] = recommendation_object
        return recommendation_object

    def _render_recommendation_group(
        self, items: Any, *, include_plex_fields: bool
    ) -> str:
        if not isinstance(items, list) or not items:
            return self.render_body("recommendations_empty")
        cards: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            plex_meta = ""
            if include_plex_fields:
                plex_meta = self.render_body(
                    "recommendation_plex_meta",
                    plex_rating_key=html.escape(str(item.get("plex_rating_key") or "")),
                    library_section_title=html.escape(
                        str(item.get("library_section_title") or "unknown")
                    ),
                )
            rotten_tomatoes_meta = self.render_body(
                "recommendation_rotten_tomatoes_meta",
                rotten_tomatoes_url=html.escape(
                    str(item.get("rotten_tomatoes_url") or "")
                ),
                rotten_tomatoes_critic_score=html.escape(
                    self._display_score(item.get("rotten_tomatoes_critic_score"))
                ),
                rotten_tomatoes_audience_score=html.escape(
                    self._display_score(item.get("rotten_tomatoes_audience_score"))
                ),
            )
            cards.append(
                self.render_body(
                    "recommendation_item",
                    title=html.escape(str(item.get("title") or "Unknown title")),
                    media_type=html.escape(str(item.get("media_type") or "unknown")),
                    year=html.escape(str(item.get("year") or "unknown")),
                    why_it_matches=html.escape(
                        str(item.get("why_it_matches") or "No reason provided.")
                    ),
                    supporting_signals=html.escape(
                        ", ".join(item.get("supporting_signals", []))
                        if isinstance(item.get("supporting_signals"), list)
                        else ""
                    ),
                    lookup_hint=html.escape(str(item.get("lookup_hint") or "")),
                    plex_meta=plex_meta,
                    rotten_tomatoes_meta=rotten_tomatoes_meta,
                )
            )
        return "".join(cards)

    def _display_score(self, value: Any) -> str:
        if isinstance(value, int):
            return f"{value}%"
        return "Unavailable"

    def _format_elapsed_duration(self, seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.0f} ms"
        if seconds < 60:
            return f"{seconds:.2f} s"
        minutes = int(seconds // 60)
        remaining_seconds = seconds - (minutes * 60)
        return f"{minutes}m {remaining_seconds:.1f}s"

    def _parse_form_body(self) -> dict[str, list[str]]:
        content_length = 0
        content_length_header = self.headers.get("Content-Length")
        if content_length_header:
            try:
                content_length = max(0, int(content_length_header))
            except ValueError:
                content_length = 0
        body = self.rfile.read(content_length) if content_length else b""
        return parse_qs(body.decode("utf-8"), keep_blank_values=True)

    def handle_artwork(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        if not token:
            self.respond_html(
                HTTPStatus.UNAUTHORIZED,
                "Login required",
                self.render_body("login_required_artwork"),
            )
            return
        path = parse_qs(parsed.query).get("path", [None])[0]
        if not isinstance(path, str) or not path.startswith("/"):
            self.respond_html(
                HTTPStatus.BAD_REQUEST,
                "Bad artwork path",
                self.render_body("bad_artwork_path"),
            )
            return
        content, content_type = self.app.plex_pms.get_artwork(token, path)
        self.respond_bytes(HTTPStatus.OK, content, content_type)

    def handle_token_json(self, parsed: Any) -> None:
        _, session = self.get_or_create_session()
        token = session.get("plex_token")
        if not token:
            self.respond_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Login required before a Plex token is available."},
            )
            return
        self.respond_json(
            HTTPStatus.OK,
            {
                "plex_token": token,
                "plex_username": session.get("plex_username"),
                "plex_account_id": session.get("plex_account_id"),
                "client_identifier": self.app.config.client_identifier,
                "pms_base_url": self.app.config.pms_base_url,
                "ready_for_plex_tv_calls": True,
            },
        )

    def get_or_create_session(self) -> tuple[str, dict[str, Any]]:
        cached = getattr(self, "_session_cache", None)
        if cached is not None:
            return cached
        cookie_header = self.headers.get("Cookie")
        session_id = None
        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            morsel = cookie.get(SESSION_COOKIE)
            if morsel:
                session_id = self.app.sessions.unsign(morsel.value)
        session = self.app.sessions.get(session_id)
        if session is None:
            session_id = self.app.sessions.create()
            session = self.app.sessions.get(session_id)
        assert session_id is not None
        assert session is not None
        self._session_cache = (session_id, session)
        return self._session_cache

    def respond_html(self, status: HTTPStatus, title: str, body: str) -> None:
        session_id, _ = self.get_or_create_session()
        self.app.sessions.save()
        payload = render_page(title, body)
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_cookie(session_id)
        self.end_headers()
        self.wfile.write(payload)

    def respond_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        session_id, _ = self.get_or_create_session()
        self.app.sessions.save()
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_cookie(session_id)
        self.end_headers()
        self.wfile.write(body)

    def respond_bytes(self, status: HTTPStatus, payload: bytes, content_type: str) -> None:
        session_id, _ = self.get_or_create_session()
        self.app.sessions.save()
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_cookie(session_id)
        self.end_headers()
        self.wfile.write(payload)

    def respond_redirect(self, location: str, session_id: str) -> None:
        self.app.sessions.save()
        self.send_response(HTTPStatus.FOUND.value)
        self.send_header("Location", location)
        self.send_cookie(session_id)
        self.end_headers()

    def send_cookie(self, session_id: str) -> None:
        cookie = SimpleCookie()
        cookie[SESSION_COOKIE] = self.app.sessions.sign(session_id)
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["httponly"] = True
        cookie[SESSION_COOKIE]["samesite"] = "Lax"
        if self.app.config.base_url.startswith("https://"):
            cookie[SESSION_COOKIE]["secure"] = True
        self.send_header("Set-Cookie", cookie.output(header="").strip())

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )
