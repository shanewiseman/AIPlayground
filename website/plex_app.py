#!/usr/bin/env python3

from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path

try:
    from .plex_config import AppConfig
    from .plex_history_summary import PlexHistorySummaryService
    from .plex_pms import PlexPmsClient
    from .plex_recommendations import PlexRecommendationService
    from .plex_routes import PlexAuthHandler, load_route_templates
    from .plex_session import SessionStore
    from .plex_tv import PlexTvClient
except ImportError:
    from plex_config import AppConfig
    from plex_history_summary import PlexHistorySummaryService
    from plex_pms import PlexPmsClient
    from plex_recommendations import PlexRecommendationService
    from plex_routes import PlexAuthHandler, load_route_templates
    from plex_session import SessionStore
    from plex_tv import PlexTvClient


class PlexAuthServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: AppConfig) -> None:
        super().__init__(server_address, PlexAuthHandler)
        self.config = config
        self.sessions = SessionStore(
            config.session_secret, config.session_store_path
        )
        self.plex_tv = PlexTvClient(config)
        self.plex_pms = PlexPmsClient(config)
        self.history_summary_service = PlexHistorySummaryService()
        self.recommendation_service = PlexRecommendationService()
        self.route_templates = load_route_templates(
            Path(__file__).resolve().parent / "templates"
        )


def main() -> None:
    config = AppConfig.from_env()
    server = PlexAuthServer((config.host, config.port), config)
    print(f"Serving Plex auth site on {config.base_url}")
    print(f"Callback URL: {config.callback_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
