# Repository Guidelines

## Project Structure & Module Organization
- `main.py`: FastAPI entrypoint (lifespan, middleware, static mount at `/ui`).
- `app/`: backend modules for API routes, Docker orchestration, queue/worker flow, infra bootstrap, auth, cleanup, and logging.
- `frontend/`: vanilla SPA files (`index.html`, `config.html`, `app.js`, `config.js`, `style.css`) served by FastAPI.
- `fallback/`: nginx fallback page/config for removed instances.
- Root operational files: `setup.sh`, `config_traefik.py`, `.env.example`, `requirements.txt`, `README.md`.

## Build, Test, and Development Commands
- Create env and install deps:
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```
- Run locally:
```bash
cp .env.example .env
python main.py
```
- Quick health check:
```bash
curl http://localhost:5050/health
```
- VPS bootstrap (production-oriented):
```bash
sudo bash setup.sh
```

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for config constants, concise module docstrings.
- JavaScript (frontend): 2-space indentation, `camelCase` for functions/variables, clear DOM id naming with kebab-case in HTML.
- Keep modules focused by domain (`routes`, `n8n`, `infra`, `worker`) and avoid cross-cutting logic in `main.py`.
- No formatter/linter is currently enforced in-repo; match existing style and keep diffs minimal.

## Testing Guidelines
- There is currently no committed `tests/` suite. For now, validate changes with:
  - API smoke checks (`/health`, `/instances`, `/capacity`).
  - UI sanity checks at `/ui/` and `/ui/config.html`.
  - Container lifecycle paths when touching `app/n8n.py` or `app/infra.py`.
- When adding tests, place them under `tests/` and name files `test_<feature>.py`.

## Commit & Pull Request Guidelines
- Follow existing Conventional Commit style from history: `feat: ...`, `fix: ...`, `docs: ...`.
- Keep commits scoped (one concern per commit) and use imperative, specific subjects.
- PRs should include:
  - What changed and why.
  - Risk/impact notes (Docker, RabbitMQ, Redis, Traefik, `.env` behavior).
  - Manual verification steps and endpoints checked.
  - UI screenshots/gifs for frontend changes.

## Security & Configuration Tips
- Never commit real secrets; use `.env.example` as template only.
- Protect `API_AUTH_TOKEN`, Cloudflare token, and RabbitMQ credentials.
- Treat config updates in `app/config.py` and `/config` endpoints as high impact; document restart requirements in PRs.
