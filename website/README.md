# Plex auth website

This directory contains a minimal Python website that sends a user through
Plex's hosted login flow and captures the resulting Plex token for later
`plex.tv` API calls.

## Module layout

- `plex_user_auth.py`: Thin entrypoint that preserves the existing run command.
- `plex_app.py`: Server bootstrap and dependency wiring.
- `plex_routes.py`: HTTP route handling and HTML responses.
- `plex_tv.py`: Plex.tv PIN login, token polling, and user profile lookup.
- `plex_pms.py`: Plex Media Server history, metadata, and artwork calls.
- `plex_history_summary.py`: `openai-agents-python` integration for structured viewing summaries.
- `plex_recommendations.py`: `openai-agents-python` integration for structured recommendation objects.
- `plex_movie_likeness_store.py`: File-backed store for movie likeness batches and ratings, separate from the signed session payload.
- `plex_api.py`: Shared Plex HTTP request helpers and error handling.
- `plex_config.py`: Environment-driven configuration.
- `plex_session.py`: Signed session store persisted to a JSON file on disk.
- `templates/`: Route body templates loaded once at server initialization.
- `library_candidates_cache.json`: Disk cache for PMS library candidates, refreshed only when a newer media key is detected.

## Run

```bash
cd /home/swiseman/git_repositories/openai-playground
APP_BASE_URL=http://127.0.0.1:8000 \
PLEX_CLIENT_IDENTIFIER=my-plex-client-id \
python3 website/plex_user_auth.py
```

Then open `http://127.0.0.1:8000`.

## Important env vars

- `APP_BASE_URL`: Public URL Plex should redirect back to. For local work use the same host/port you run on.
- `PLEX_CLIENT_IDENTIFIER`: Stable identifier for your app/device in Plex.
- `PLEX_PRODUCT`: Display name Plex shows to the user.
- `PLEX_VERSION`: App version sent in Plex headers.
- `PLEX_PLATFORM`: Usually `Web`.
- `PLEX_DEVICE_NAME`: Friendly device/app name.
- `PLEX_PMS_BASE_URL`: Plex Media Server base URL. Defaults to `http://10.1.0.67:32400`.
- `PLEX_HISTORY_ITEM_LIMIT`: Number of recent history items to render. Defaults to `8`.
- `PLEX_LIBRARY_CANDIDATE_LIMIT`: Number of Plex library items considered for on-server recommendations. Defaults to `80`.
- `OPENAI_API_KEY`: Required for the `/summary` route.
- `OPENAI_AGENT_MODEL`: Optional model override for the summary agent. Defaults to `gpt-5-mini`.
- `SESSION_SECRET`: Optional. If unset, a stable secret is generated and stored in the session store file so cookies continue working across restarts.
- `SESSION_STORE_PATH`: Path to the JSON file used to persist sessions across restarts. Defaults to `website/session_store.json`.
- `MOVIE_LIKENESS_STORE_PATH`: Path to the JSON file used to persist movie likeness batches and ratings. Defaults to `website/movie_likeness_store.json`.

## Flow

1. `GET /login` renders the Plex login/status page.
2. `GET /login/start` creates a Plex PIN via `https://plex.tv/api/v2/pins?strong=true`.
3. The browser is redirected to `https://app.plex.tv/auth` with the PIN code and `forwardUrl`.
4. Plex sends the browser back to `/auth/plex/callback`.
5. The app exchanges the authorized PIN for `authToken`, looks up the Plex user profile, and stores the token plus account id in the session.
6. `GET /history` queries `http://10.1.0.67:32400/status/sessions/history/all` with `accountID=<logged-in account id>` and sorts by `viewedAt:desc`.
7. Each history item is enriched from its `/library/metadata/...` key so the page can render associated artwork through the local `/artwork` proxy route.
8. `GET /summary` sends the enriched history through the local `openai-agents-python` SDK and returns a database-friendly structured summary object.
9. `GET /recommendations` uses the viewing summary plus current Plex library candidates to produce two database-friendly recommendation lists: already on Plex and not on Plex.
10. `GET /account` displays the current Plex account id and `GET /token.json` returns the token payload for server-side use.
