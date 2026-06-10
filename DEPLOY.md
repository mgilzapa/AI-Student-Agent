# Deployment (Railway)

This app is a long-running FastAPI/uvicorn server. All persistent state lives in
Supabase (Postgres + pgvector + Storage); the local `data/` dir and tempfiles are
ephemeral scratch only. That makes it stateless and a clean fit for Railway.

> **Why Railway and not Vercel?** The app needs persistent processes (background
> document processing in a thread pool), SSE streaming (`/ask/stream`), large
> dependencies, and 50 MB uploads — all of which Vercel's serverless model
> handles poorly (function timeouts, frozen background work, bundle-size limits).
> Railway runs the actual container persistently and lets you pin the region.

## One-time: rotate secrets first ⚠️

The OpenAI, Anthropic and Supabase service keys have been exposed in plaintext.
**Regenerate all three before deploying** and use the new values below:
- OpenAI: platform.openai.com → API keys → revoke + create
- Anthropic: console.anthropic.com → API keys → revoke + create
- Supabase: Project Settings → API → roll the `service_role` key

## Steps

1. **Push to GitHub** (the repo already ignores `.env`, so no secrets ship).
2. **Create the project:** Railway → New Project → Deploy from GitHub repo.
   Nixpacks auto-detects Python via `.python-version` (3.10) and installs
   `requirements.txt`. The start command + healthcheck come from `railway.json`.
3. **Pick the region** closest to your Supabase project (Settings → Region).
   This is the single biggest latency win — Supabase round-trips drop from
   ~70–180 ms to ~5–20 ms.
4. **Set environment variables** (Variables tab) — see `.env.example` for the
   full annotated list. Minimum required:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
   - **Do NOT set `DEV_NO_AUTH`** — leaving it unset keeps auth enforced.
5. **First deploy.** Railway assigns a domain (Settings → Networking → Generate
   Domain). The healthcheck hits `/` (public, no token) and should pass.
6. **Lock CORS.** Add `FRONTEND_ORIGIN=https://<your-domain>` and redeploy, so
   the API only accepts browser calls from your own frontend.

## Notes

- **Single worker** is intended: the rate limiter uses in-memory storage. If you
  ever scale to multiple uvicorn workers, switch `RATELIMIT_STORAGE_URI` to a
  Redis URL (add a Railway Redis plugin) — otherwise limits are per-worker.
- The start command lives in both `railway.json` and `Procfile` (the latter is a
  portable fallback for Render/Heroku-style platforms).
- Local dev/test deps (`pytest`) are in `requirements-dev.txt` and are not
  installed in production.
