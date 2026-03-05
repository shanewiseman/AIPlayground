# Plex Auth Website

This directory contains a Python web app that:
- runs the Plex hosted login flow,
- stores the Plex token in a signed cookie-backed session,
- fetches/enriches Plex history and library metadata,
- generates AI viewing summaries and recommendations,
- collects explicit 1-5 movie likeness ratings and maintains an evolving commonality summary.

## Module Layout

- `plex_user_auth.py`: Thin entrypoint used by the existing run command.
- `plex_app.py`: Server bootstrap and dependency wiring.
- `plex_routes.py`: HTTP route handling and HTML responses.
- `plex_tv.py`: Plex.tv PIN login, token polling, and profile lookup.
- `plex_pms.py`: PMS history, metadata, artwork proxy data, and library candidate cache.
- `plex_history_summary.py`: Structured viewing summary generation.
- `plex_movie_likeness.py`: Movie likeness batch rendering and rating submit handling.
- `plex_movie_likeness_commonality.py`: Incremental commonality-summary generation from rated movies.
- `plex_recommendations.py`: Structured on-server and off-server recommendations.
- `plex_session.py`: Signed session store persisted to disk.
- `plex_movie_likeness_store.py`: File-backed store for movie likeness state.
- `plex_agent_instructions.py`: Centralized prompt instructions used by agent-backed services.
- `plex_file_io.py`: Atomic, lock-protected backend file writes.
- `plex_api.py`: Shared Plex HTTP request helpers and error handling.
- `plex_config.py`: Environment-driven configuration and defaults.
- `templates/`: Route templates loaded once at server startup.

## Run

```bash
cd /home/swiseman/git_repositories/openai-playground
APP_BASE_URL=http://127.0.0.1:8000 \
PLEX_CLIENT_IDENTIFIER=my-plex-client-id \
python3 website/plex_user_auth.py
```

Open `http://127.0.0.1:8000`.

## Current Capabilities

- Plex login/logout flow via PIN (`/login`, `/login/start`, `/auth/plex/callback`, `/logout`).
- Account/token inspection (`/account`, `/token.json`).
- Recent history page with enriched metadata and artwork proxy (`/history`, `/artwork`).
- AI viewing summary generation with session caching (`/summary`, `?refresh=1` to regenerate).
- AI recommendations generation with session caching (`/recommendations`, `?refresh=1` to regenerate).
- Movie likeness workflow:
- `GET /movie-likeness` shows an unrated movie batch from the local Plex library.
- `POST /movie-likeness` saves ratings and updates commonality summary from newly rated items.

## Default Arguments and Environment Variables

- `HOST`: `127.0.0.1`
- `PORT`: `8000`
- `APP_BASE_URL`: `http://{HOST}:{PORT}` if unset
- `PLEX_CLIENT_IDENTIFIER`: `openai-playground-plex-demo`
- `PLEX_PRODUCT`: `OpenAI Playground Plex Demo`
- `PLEX_VERSION`: `0.1.0`
- `PLEX_PLATFORM`: `Web`
- `PLEX_DEVICE_NAME`: `OpenAI Playground`
- `PLEX_PMS_BASE_URL`: `http://10.1.0.67:32400`
- `PLEX_PIN_POLL_ATTEMPTS`: `12`
- `PLEX_PIN_POLL_INTERVAL_SECONDS`: `1.0`
- `PLEX_HISTORY_ITEM_LIMIT`: `100`
- `PLEX_LIBRARY_CANDIDATE_LIMIT`: `4000`
- `SESSION_SECRET`: optional. If unset, a stable secret is generated and persisted.
- `SESSION_STORE_PATH`: defaults to `website/state/session_store.json`
- `MOVIE_LIKENESS_STORE_PATH`: defaults to `website/state/movie_likeness_store.json`
- `OPENAI_API_KEY`: required for `/summary`, `/recommendations`, and movie likeness commonality updates.
- `OPENAI_AGENT_MODEL`: optional global override for all agent calls.
- If `OPENAI_AGENT_MODEL` is unset:
- `/summary` and movie-likeness commonality use `gpt-5-mini`.
- `/recommendations` uses `gpt-5-nano`.

## Data and Caching

- Session data is persisted in `state/session_store.json`.
- Movie likeness state is persisted in `state/movie_likeness_store.json`.
- PMS library candidate cache is persisted in `state/library_candidates_cache.json`.
- Agent debug artifacts are written under `debug_output/`:
- recommendation prompts, candidate payload, token estimates, and final output
- movie likeness commonality prompt and final output
- Backend file writes are atomic and lock-protected to avoid concurrent write corruption.

## Route Flow

1. `GET /login` shows login state.
2. `GET /login/start` creates a Plex PIN and redirects to Plex auth.
3. Plex redirects to `GET /auth/plex/callback`.
4. Callback polls PIN status, stores `plex_token`, `plex_username`, and `plex_account_id`.
5. `GET /history` fetches and enriches recent history from PMS.
6. `GET /summary` summarizes enriched history and caches it in session.
7. `GET /movie-likeness` builds a batch of unrated local movies for 1-5 scoring.
8. `POST /movie-likeness` saves ratings, updates commonality, and clears the batch.
9. `GET /recommendations` combines summary + commonality + library candidates to generate on/off-server recommendations.
