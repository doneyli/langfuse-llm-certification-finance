# Hosting Plan — Public Demo on Vercel + Langfuse Cloud

> **Status:** design / implementation plan for [issue #2](../../issues/2). This
> document does **not** deploy anything or commit any credentials — it resolves the
> open questions with recommended defaults and the trade-offs behind them, so the
> deployment can be executed (and reviewed) as a follow-up.

## Goal (from #2)

Make this repo available as a **hosted demo** so (1) fellow Solutions Architects can
open a live instance with zero local setup, and (2) customers can see the demo
before deploying it on their own data. The repo stays **public** and
customer-friendly; the hosted instance points at **Langfuse Cloud** instead of a
self-hosted Langfuse.

---

## 0. TL;DR — recommended defaults

| Decision | Recommendation |
|---|---|
| **Repo strategy** | **One public repo**, hosting driven by env vars + a thin Vercel config. No second repo; secrets live only in Vercel project settings. |
| **Langfuse target** | A dedicated **Langfuse Cloud** project ("cert-demo"), seeded once with the sample datasets and pre-computed runs by the existing `setup_*.py` / `run_*.py` scripts. |
| **Vercel shape** | **Static SPA on Vercel + the read-only `/api/*` as a Vercel Python Serverless Function** (the portal's reads are stateless and cache-friendly). |
| **Env separation** | Hosted config = Vercel env vars; customers keep using `.env` + `selfhost/`. Make the portal's `DATASETS` list env-configurable (small code change, noted below). |
| **Access control** | **Gate it** with Vercel's built-in password protection (or a shared basic-auth token). Not fully public on day one, because the dashboard backend holds a Langfuse secret key. |
| **Data** | **Fixed, pre-seeded sample datasets + pre-computed runs** for the public surface (deterministic, no LLM keys on the public path). "Live run" stays an SA-only, authenticated path. |

The single most important architectural fact driving all of this: **the portal is
not a static site.** `portal/langfuse_client.py` reads Langfuse with the
**secret** key server-side and aggregates it. So there must be a server (or
serverless function) that holds `LANGFUSE_SECRET_KEY` — the SPA alone cannot talk
to Langfuse directly, and we would not want it to (that key must never reach the
browser).

---

## 1. What we are actually deploying

```
                         Vercel project (cert-demo)
   ┌───────────────────────────────────────────────────────────┐
   │  Static SPA  (portal/frontend/dist — Vite/React/Click UI)   │
   │     │  fetch /api/dashboard, /api/breakdown/..., /api/run/.. │
   │     ▼                                                        │
   │  Python Serverless Function  (wraps portal/app.py FastAPI)   │
   │     • holds LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL      │
   │     • read-only: dashboard / breakdown / history / run       │
   └───────────────────────────┬─────────────────────────────────┘
                                │ Langfuse REST + SDK (reads)
                                ▼
                    Langfuse Cloud  (project: cert-demo)
                    datasets + experiment runs + scores
                                ▲
                                │ one-time seed (writes), run locally / in CI — NOT on the public path
            setup_datasets.py · setup_prompts.py · setup_score_configs.py ·
            run_certification.py · run_usecase_certification.py
```

Two distinct credential planes, and keeping them separate is the security crux:

- **Public path (read):** the deployed app needs only Langfuse keys with **read**
  scope. It never needs `ANTHROPIC_API_KEY` and never writes to Langfuse.
- **Seeding/cert path (write + LLM):** `setup_*` and `run_*` need Langfuse write
  access **and** `ANTHROPIC_API_KEY`. These run **off** the public surface — locally
  by an SA, or in a gated CI job — never as a public HTTP endpoint.

---

## 2. Decision-by-decision

### 2.1 Repo strategy — one public repo (recommended)

Keep a single public repo. A separate private "deploy repo" is **not** needed
because nothing secret has to live in source:

- Vercel reads `LANGFUSE_*` from **project environment variables**, not the repo.
- The hosted vs. self-hosted difference is entirely env-driven (`LANGFUSE_BASE_URL`
  + keys), which `.env.example` already models.

Trade-off considered: a private overlay repo would let us pin demo-specific config
and a curated dataset snapshot away from customers. But that config is a handful of
env vars and a seed script invocation — not worth the split-brain maintenance cost
or the risk of the public repo drifting from what's actually demoed. **Verdict:
one repo.** If a private snapshot of *data* is ever needed, that belongs in the
Langfuse Cloud project, not a second repo.

### 2.2 Langfuse Cloud migration

Almost nothing in code changes — the SDK and REST layer already honor
`LANGFUSE_BASE_URL` and fall back to `https://cloud.langfuse.com`. Steps:

1. Create a Langfuse Cloud org + project (e.g. **cert-demo**); pick the region and
   set `LANGFUSE_BASE_URL` accordingly (`https://cloud.langfuse.com` EU or
   `https://us.cloud.langfuse.com` US).
2. Generate two key pairs in that project:
   - a **read-scoped** pair for the deployed dashboard, and
   - a write-capable pair used only for the one-time seed (kept local/CI).
   *(If Langfuse Cloud does not expose per-key read-only scoping at the time of
   setup, use a project dedicated to the demo so the blast radius is just demo data
   — see §2.5.)*
3. Seed the project once, pointed at Cloud:
   ```bash
   export LANGFUSE_BASE_URL=https://cloud.langfuse.com
   export LANGFUSE_PUBLIC_KEY=pk-... LANGFUSE_SECRET_KEY=sk-...   # write pair
   export ANTHROPIC_API_KEY=sk-ant-...
   uv run python setup_datasets.py --dataset all --sample
   uv run python setup_datasets.py --dataset advisory-adversarial
   uv run python setup_prompts.py
   uv run python setup_score_configs.py
   # Pre-compute the runs the dashboard will show (model + use-case cert):
   uv run python run_certification.py --dataset certification/financebench-sample --model claude-sonnet-4-6
   uv run python run_usecase_certification.py --use-case 10k-analyst      --dataset certification/financebench-sample --model claude-sonnet-4-6
   uv run python run_usecase_certification.py --use-case sentiment-triage --dataset certification/fpb-sample            --model claude-sonnet-4-6
   uv run python run_usecase_certification.py --use-case advisory-draft   --dataset certification/advisory-adversarial --model claude-sonnet-4-6
   ```
4. Verify the dashboard locally against Cloud (`python -m portal.app`) before
   deploying — same code, just different env.

### 2.3 Vercel deployment

The portal is a unified FastAPI app that serves the built SPA and the `/api/*`
reads (same origin → no CORS needed). Two shapes are viable:

- **(A) SPA on Vercel + `/api/*` as a Vercel Python Serverless Function** —
  *recommended.* Add an `api/index.py` that exposes the existing
  `portal.app:app` ASGI app through Vercel's Python runtime (a 3–5 line adapter),
  build the SPA as a static output, and add `vercel.json` rewrites so `/api/*`
  hits the function and everything else serves the SPA. The portal's endpoints are
  stateless, cached (`TTLCache`, 60s) and paginated, so they fit serverless limits
  comfortably. This keeps the whole thing on Vercel — the issue's leading option.

  ```jsonc
  // vercel.json (sketch — not wired in this PR)
  {
    "buildCommand": "cd portal/frontend && npm install && npm run build",
    "outputDirectory": "portal/frontend/dist",
    "rewrites": [{ "source": "/api/(.*)", "destination": "/api/index" }]
  }
  ```

- **(B) Single container on Render/Railway/Fly** — deploy `uvicorn portal.app:app`
  as-is (zero adapter code; identical to local). Simplest mental model, but it is
  not Vercel. Good fallback if the serverless adapter proves fiddly or if we want a
  long-lived process.

**Verdict:** start with **(A)** to satisfy "Vercel"; keep **(B)** documented as the
no-surprises fallback. Either way the SPA build step (`npm run build`) must run in
the deploy pipeline — the dist is git-ignored.

### 2.4 Environment separation (hosted demo vs. customer self-host)

- **Hosted demo:** all config via Vercel env vars — `LANGFUSE_BASE_URL`,
  `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (read pair). No `.env` in the deploy.
- **Customer self-host:** unchanged — they clone, copy `.env.example`, point at
  their own Cloud or `selfhost/` Langfuse, and run locally. The README's
  "Choose your Langfuse deployment" section already covers this.
- **Small code change to make this clean (do in the implementation PR, not here):**
  `portal/langfuse_client.py` hardcodes the `DATASETS` list. Make it
  env-overridable (e.g. `PORTAL_DATASETS` comma-separated, defaulting to today's
  list) so the hosted demo can curate exactly which datasets/rows appear without a
  code edit. Optionally add a `DEMO_MODE` flag that hides any write/trigger affordance.

### 2.5 Access control

The deployed backend holds a Langfuse **secret** key and renders internal-ish
evaluation data, so **do not ship it fully open on day one.** Options, simplest
first:

1. **Vercel password protection** (Pro feature, zero code) — a single shared
   password in front of the whole deployment. Best default for an SA-shared demo.
2. **Shared basic-auth token** in the serverless function (a few lines, works on any
   plan) — gate `/` and `/api/*` behind one env-configured token.
3. **Fully public** — acceptable **only** if the Langfuse Cloud project is
   demo-dedicated, the dashboard key is read-scoped, and the data is non-sensitive
   sample data. Even then, prefer (1)/(2) for customers-before-commit so the URL
   isn't crawlable.

**Verdict:** gate with (1) for the SA audience; promote to (3) public-read only for
a deliberately curated, read-scoped, demo-only project. The **write/cert path is
never exposed regardless** — it has no public route.

### 2.6 Data / datasets

- **Public surface = fixed, pre-seeded data + pre-computed runs** (§2.2 step 3).
  Deterministic, fast, no LLM keys on the public path, no per-visit cost.
- **Live runs = SA-only, authenticated, off the public app.** Triggering a
  certification needs Langfuse **write** + `ANTHROPIC_API_KEY`; that belongs in a
  local run or a gated CI `workflow_dispatch`, not a public button. If we later want
  an in-app "run it live" demo, it must sit behind auth (§2.5) and use a
  server-side, budget-capped key — called out as a future enhancement, not v1.

---

## 3. Security checklist (must hold before any deploy)

- [ ] No Langfuse/Anthropic keys in the repo, git history, or the built `dist`.
      (`.env` is git-ignored; keys live only in Vercel project settings.)
- [ ] The deployed dashboard uses a **read-scoped** Langfuse key (or a
      demo-dedicated project) — never a write key.
- [ ] `ANTHROPIC_API_KEY` is **absent** from the deployed environment (public path
      never calls an LLM).
- [ ] No public route invokes `setup_*` / `run_*` (no write/seed endpoint exists).
- [ ] Access gating (Vercel password or basic-auth) enabled for the SA demo.
- [ ] `LANGFUSE_SECRET_KEY` is only ever read server-side; confirm it is never sent
      to the browser (the SPA only calls our `/api/*`, which already proxies reads).

---

## 4. Implementation checklist (follow-up PR)

1. `api/index.py` ASGI adapter exposing `portal.app:app` on Vercel's Python runtime.
2. `vercel.json` (build command, output dir, `/api/*` rewrite) — sketch in §2.3.
3. Make `PortalClient.DATASETS` env-configurable (`PORTAL_DATASETS`); optional
   `DEMO_MODE`.
4. Create the Langfuse Cloud **cert-demo** project; seed datasets + runs (§2.2).
5. Set Vercel env vars (read-scoped Langfuse keys + `LANGFUSE_BASE_URL`).
6. Enable access gating (§2.5); deploy; verify the three `usecase:*` rows + model
   rows render against Cloud.
7. README: add a short "Live demo" pointer (URL + that it's read-only sample data).

---

## 5. Open items genuinely needing an owner decision

These are *not* blockers for this plan, but the deploying SA should choose:

- **Region:** Langfuse Cloud EU vs. US (data-residency / latency for the audience).
- **Audience reach:** SA-only (gated) first, or also customer-public (read-scoped)?
- **Live-run demo:** ship v1 read-only (recommended), or invest in the gated,
  budget-capped live-run path now?
- **Vercel org/project ownership:** which team account hosts it and pays for Pro
  (needed for built-in password protection).
