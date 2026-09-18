# Cogitator-Optimus — Smart Campus Energy Optimization

> *Grid decisions, spoken in natural language.*

[![BUP CSE Fest 2026](https://img.shields.io/badge/BUP-CSE%20Fest%202026-d10000?style=for-the-badge)](#)
[![Track: GridWise](https://img.shields.io/badge/Track-GridWise-1a1a1a?style=for-the-badge&logo=lightning&logoColor=d10000)](#)
[![Stack: FastAPI](https://img.shields.io/badge/Stack-FastAPI%20%2B%20Pydantic%20%2B%20PuLP-0a0a0a?style=for-the-badge&logo=fastapi&logoColor=d10000)](#)

A production-shaped FastAPI backend that turns **free-form operator notes** into a
**minimum-cost, 24-hour campus energy schedule**, using an LLM to interpret
language, Pydantic v2 to enforce a strict contract, and PuLP linear programming
to solve the dispatch.

- **One business endpoint** — `POST /optimize-energy`.
- **Themed docs** — black-and-red Swagger UI at `/docs`, themed ReDoc at `/redoc`.
- **Black-and-red landing page** at `/`.
- **Top-level safety net** — every request is wrapped in `try/except`. The API
  never crashes; it returns a clean HTTP 500 with diagnostics.

---

## Table of Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Quickstart](#quickstart)
4. [Environment variables](#environment-variables)
5. [API reference](#api-reference)
6. [Directive types](#directive-types)
7. [Auto-healing rules](#auto-healing-rules)
8. [Optimization model](#optimization-model)
9. [Walkthrough](#walkthrough)
10. [Docker](#docker)
11. [Project layout](#project-layout)
12. [Security](#security)
13. [Troubleshooting](#troubleshooting)

---

## What it does

A campus energy manager writes 1–3 plain-English notes like:

> *"Wash the solar panels from noon to 2 PM — treat usable solar as 25%."*
> *"Lock battery reserve above 80 kWh during the 6 PM evening peak."*
> *"Sports office moved next month's registration deadline."* (irrelevant)

The API returns a typed JSON plan:

| Field                       | Meaning                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------ |
| `directive_interpretation`  | Which notes became which directives, and why.                                        |
| `hourly_plan`               | 24 entries — per hour: demand, solar used, grid, charge/discharge, battery SoC, tariff, action. |
| `total_grid_kwh`            | Sum of grid draw over 24h (independently recomputed from the plan).                  |
| `total_cost_bdt`            | Sum of `grid_kwh × tariff` over 24h.                                                 |
| `peak_grid_kwh`             | Max hourly grid draw.                                                                |
| `plan_summary`              | One-sentence human-readable summary.                                                 |

It is **safe under bad LLM output** — malformed JSON, missing fields, out-of-range
hours, etc. are absorbed by the auto-healing validators and either repaired or
silently downgraded to `no_op`.

---

## Architecture

Five phases, one HTTP call.

```
┌──────────────────────────────────────────────────────────────────┐
│  POST /optimize-energy                                          │
│  body = ScenarioRequest + operator_notes[]                      │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
   ┌────────────────────────────────────────────────────┐
   │  Phase 1  ·  LLM Extraction                       │
   │  POST {LLM_BASE_URL}/chat/completions             │
   │   → raw JSON (often messy)                        │
   └────────────────────────────┬───────────────────────┘
                                │  raw directive objects
                                ▼
   ┌────────────────────────────────────────────────────┐
   │  Phase 2  ·  Pydantic Auto-Healer  (schemas.py)   │
   │   • hour array: dedupe + sort + clamp 0..23       │
   │   • no_op  → applies=False & adjustment=None      │
   │   • solar factor  → clamp into [0.0, 1.0]         │
   │   • extras ignored                                │
   └────────────────────────────┬───────────────────────┘
                                │  ExtractedDirective[]
                                ▼
   ┌────────────────────────────────────────────────────┐
   │  Phase 3  ·  Directive-Aware Parameterisation     │
   │  Each directive overlays a bound on the LP:       │
   │   • solar_reduction[h]   = forecast × factor      │
   │   • minimum_battery_reserve[h] ≥ k                │
   │   • no_charge_window[h]  = 0                      │
   │   • no_discharge_window[h] = 0                    │
   │   • max_grid_window[h]   ≤ K                      │
   └────────────────────────────┬───────────────────────┘
                                ▼
   ┌────────────────────────────────────────────────────┐
   │  Phase 4  ·  PuLP LP  (solver.py)                 │
   │  minimise  Σ grid[h] · tariff[h]                  │
   │  subject to:                                      │
   │   • per-hour balance (supply = demand)            │
   │   • battery SoC bounds (min..capacity)            │
   │   • charge / discharge rate caps                  │
   │   • end-of-day SoC = initial SoC (neutrality)     │
   │   • all directive overlays                        │
   │  Solver chain: HiGHS_CMD → CBC → GLPK            │
   └────────────────────────────┬───────────────────────┘
                                │  OptimizationResult
                                ▼
   ┌────────────────────────────────────────────────────┐
   │  Phase 5  ·  Response Formulation                 │
   │   • recompute totals from the plan (no LP reuse)  │
   │   • emit OptimizeResponse (strict)                │
   │   • wrap in try/except → HTTP 500 on crash        │
   └────────────────────────────────────────────────────┘
```

The full request lifecycle is single-coroutine, async I/O. The CPU-bound PuLP
solve is sync inside the coroutine — small enough (24 vars × 24h) that the GIL
never becomes a problem at this scale.

---

## Quickstart

> Requires **Python 3.11+**. Tested on Windows 11 with Python 3.14 and on
> Ubuntu 22.04 with Python 3.12.

```powershell
# 1. Clone & enter
git clone <your-repo-url> cogitator-optimus
cd cogitator-optimus

# 2. Create & activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows / PowerShell
# source .venv/bin/activate           # macOS / Linux

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure environment (Windows PowerShell — adjust per OS)
$env:LLM_API_KEY = "sk-or-v1-your-key-here"   # OpenRouter example
# or
$env:LLM_API_KEY = "gsk_your-groq-key"        # Groq example

# 5. Run the server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Once running, open:

- **Landing page** → <http://localhost:8000/>
- **Swagger UI** → <http://localhost:8000/docs>
- **ReDoc** → <http://localhost:8000/redoc>
- **OpenAPI JSON** → <http://localhost:8000/openapi.json>

---

## Environment variables

### Required

| Variable         | Purpose                                                            |
| ---------------- | ------------------------------------------------------------------ |
| `LLM_API_KEY`    | Bearer token for the LLM provider.                                 |
| `OPENAI_API_KEY` | Alias accepted if `LLM_API_KEY` is not set.                        |

### Optional

| Variable              | Default                       | Purpose                                                |
| --------------------- | ----------------------------- | ------------------------------------------------------ |
| `LLM_BASE_URL`        | `https://api.openai.com/v1`   | Any OpenAI-compatible chat-completions base URL.       |
| `LLM_MODEL`           | `gpt-4o-mini`                 | Model name passed to `/chat/completions`.              |
| `LLM_TIMEOUT_SECONDS` | `30`                          | HTTP timeout for LLM calls.                            |

### Provider auto-detection

If `LLM_BASE_URL` is left at its default, the server inspects the API key prefix
and auto-routes:

| Key prefix | Provider   | Auto base URL                      | Auto model              |
| ---------- | ---------- | ---------------------------------- | ----------------------- |
| `gsk_`     | Groq       | `https://api.groq.com/openai/v1`   | `openai/gpt-oss-20b`    |
| `sk-or-`   | OpenRouter | `https://openrouter.ai/api/v1`     | `openai/gpt-4o-mini`    |
| `sk-`      | OpenAI     | `https://api.openai.com/v1`        | `gpt-4o-mini`           |

You can always override `LLM_BASE_URL` and `LLM_MODEL` explicitly.

### Example `.env`

Create a `.env` (never commit it — it is in `.gitignore`):

```env
LLM_API_KEY=sk-or-v1-your-key-here
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4o-mini
```

---

## API reference

### `GET /health`

Liveness probe. Returns `200 OK`.

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

### `POST /optimize-energy`

The single business endpoint.

**Request body** — `ScenarioRequest`:

```json
{
  "scenario_id": "SAMPLE-01",
  "battery_capacity_kwh": 220.0,
  "initial_energy_kwh": 110.0,
  "min_energy_kwh": 40.0,
  "max_charge_kwh_per_hour": 50.0,
  "max_discharge_kwh_per_hour": 50.0,
  "demand_kwh": [ /* 24 floats, hours 0..23 */ ],
  "solar_kwh":   [ /* 24 floats */ ],
  "tariff_bdt_per_kwh": [ /* 24 floats */ ],
  "operator_notes": [
    "Wash the solar panels from noon to 2 PM, treat solar as 25%",
    "Sports office moved next month's registration deadline."
  ]
}
```

| Field                        | Type          | Constraints                                          |
| ---------------------------- | ------------- | ---------------------------------------------------- |
| `scenario_id`                | string        | required                                             |
| `battery_capacity_kwh`       | float         | `> 0`                                                |
| `initial_energy_kwh`         | float         | `≥ 0`                                                |
| `min_energy_kwh`             | float         | `≥ 0`, default `0.0`                                 |
| `max_charge_kwh_per_hour`    | float         | `≥ 0`                                                |
| `max_discharge_kwh_per_hour` | float         | `≥ 0`                                                |
| `demand_kwh`                 | float[24]     | non-negative                                         |
| `solar_kwh`                  | float[24]     | non-negative                                         |
| `tariff_bdt_per_kwh`         | float[24]     | non-negative                                         |
| `operator_notes`             | string[0..3]  | at most 3 notes                                      |

**Response body** — `OptimizeResponse` (200 OK):

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "note_text": "Wash the solar panels from noon to 2 PM…",
      "directive_type": "solar_reduction",
      "applies": true,
      "reasoning": "Cleaning reduces usable solar from 12:00 to 14:00…",
      "structured_adjustment": { "hours": [12, 13], "factor": 0.25 }
    },
    {
      "note_index": 1,
      "note_text": "Sports office moved next month's registration deadline.",
      "directive_type": "no_op",
      "applies": false
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,  "demand_kwh": 42.0, "solar_used_kwh": 0.0,
      "grid_kwh": 28.0, "charge_kwh": 14.0, "discharge_kwh": 0.0,
      "battery_action": "charge", "battery_kwh": 110.0, "battery_energy_after_kwh": 124.0,
      "tariff_bdt_per_kwh": 8.5
    }
    /* … 24 entries total … */
  ],
  "total_grid_kwh": 1371.75,
  "total_cost_bdt": 14125.75,
  "peak_grid_kwh": 129.25,
  "plan_summary": "Optimized 24h schedule for scenario SAMPLE-01. …"
}
```

**Errors**:

| Status | When                                                                  |
| ------ | --------------------------------------------------------------------- |
| `422`  | Pydantic validation rejected the request body.                        |
| `500`  | Anything else. A descriptive `detail` field is returned. The process never crashes. |

---

## Directive types

The LLM is constrained (via the system prompt) to emit **one of**:

| `directive_type`            | What it does                                                | `structured_adjustment`                                       |
| --------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------- |
| `solar_reduction`           | Scale solar forecast in listed hours by `factor`.           | `{ "hours": [12,13], "factor": 0.25 }`                        |
| `minimum_battery_reserve`   | Battery SoC must stay `≥ k` in listed hours.                | `{ "hours": [...], "minimum_energy_kwh": 80 }`                |
| `no_charge_window`          | Force `charge_kwh = 0` in listed hours.                     | `{ "hours": [...] }`                                          |
| `no_discharge_window`       | Force `discharge_kwh = 0` in listed hours.                  | `{ "hours": [...] }`                                          |
| `max_grid_window`           | Cap grid draw to `K` kWh in listed hours.                   | `{ "hours": [...], "max_grid_kwh": 60 }`                      |
| `no_op`                     | Note is not actionable on the energy schedule.              | `null`                                                        |

---

## Auto-healing rules

`schemas.py` performs the following repairs **before** the solver runs:

| Rule | Validator                  | Behavior                                                                  |
| ---- | -------------------------- | ------------------------------------------------------------------------- |
| 1    | `_heal_adjustment`         | Hours are deduplicated, sorted, and clamped to `0..23`.                    |
| 2    | `_heal_adjustment`         | `solar_reduction` factor clamped to `[0.0, 1.0]`.                         |
| 3    | `_enforce_applies_rule`    | If `directive_type == no_op`, `applies = false` and `adjustment = null`.  |
| 4    | `_enforce_applies_rule`    | If `directive_type != no_op`, `applies = true`.                           |
| 5    | `extra="ignore"`           | Extra fields in the LLM output are silently dropped.                      |
| 6    | top-level try/except       | Any unrecoverable parse error becomes a `no_op` row.                      |

The intent: **the solver never sees garbage**. Even if the LLM hallucinates or
formats wrong, the API still emits a valid `OptimizeResponse`.

---

## Optimization model

Formally, given 24 hours `h = 0..23`:

```
minimise   Σ_h  grid[h] · tariff[h]

subject to
  balance[h]   : grid[h] + solar_used[h] + discharge[h] − charge[h] = demand[h]
  soc_min[h]   : battery_energy_after[h] ≥ min_energy_kwh
  soc_max[h]   : battery_energy_after[h] ≤ battery_capacity_kwh
  soc_link[h]  : battery_energy_after[h] = battery_energy_after[h−1]
                                       + charge[h] · η_c − discharge[h]
                                       − loss[h]
  rate_c[h]    : 0 ≤ charge[h] ≤ max_charge_kwh_per_hour
  rate_d[h]    : 0 ≤ discharge[h] ≤ max_discharge_kwh_per_hour
  solar[h]     : 0 ≤ solar_used[h] ≤ solar_forecast[h]
  soc_eod      : battery_energy_after[23] = initial_energy_kwh          (neutrality)

  + one extra constraint per active directive (see table above).
```

Solver chain in `solver.py`:

1. `HiGHS_CMD` — bundled with PuLP 2.7+ as a wheel, no system install needed.
2. `COIN_CMD` — requires `coinor-cbc` on `PATH` (installed in Docker image).
3. `GLPK_CMD` — requires `glpk-utils`.

---

## Walkthrough

A reference payload is provided in `sample_input.json` (scenario `SAMPLE-01`,
2 notes, 24 hours, BDT-denominated tariffs).

```bash
curl -s -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  --data @sample_input.json
```

Expected behavior of the system on `SAMPLE-01`:

- Note 0 ("panels 12–2, treat solar as 25%") → `solar_reduction`, hours `[12,13]`, factor `0.25`.
- Note 1 ("sports office moved deadline") → `no_op` (not energy-related).
- Solar at hours 12 and 13 is capped at `forecast × 0.25` (≈ 28.75 and 27.5 kWh).
- Battery ends the day at the same SoC it started (110 kWh) — `soc_eod` constraint.
- Response `peak_grid_kwh` is **lower** than the equivalent un-optimized baseline for hours 18–20.

---
## Docker

A fallback image is shipped for judges on systems where Python/pip is awkward.

```bash
# Build
docker build -t cogitator-optimus:latest .

# Run (inject LLM_API_KEY at runtime — never bake it into the image)
docker run --rm -p 8000:8000 \
  -e LLM_API_KEY="$LLM_API_KEY" \
  -e LLM_BASE_URL="https://api.openai.com/v1" \
  -e LLM_MODEL="gpt-4o-mini" \
  --name cogitator-optimus-api \
  cogitator-optimus:latest
```

Then:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  --data @sample_input.json
```

The image installs `coinor-cbc`, runs as a non-root user, exposes `8000`, and
includes a built-in `HEALTHCHECK`.

---

## Project layout

```
.
├── main.py              # FastAPI app, LLM client, pipeline orchestrator
├── schemas.py           # Pydantic v2 models + auto-healing validators
├── solver.py            # PuLP LP (decision vars, constraints, EoD neutrality)
├── requirements.txt     # Pinned Python dependencies
├── sample_input.json    # Reference scenario (SAMPLE-01) for judges
├── Dockerfile           # python:3.11-slim + coinor-cbc + uvicorn
├── .gitignore           # Excludes .env, venvs, caches, IDE state
└── README.md            # This file
```

| File               | Purpose                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `main.py`          | FastAPI app, `/health`, `/optimize-energy`, LLM call, JSON extraction, themed docs.      |
| `schemas.py`       | All request/response models and the auto-healing rules.                                  |
| `solver.py`        | Pure-PuLP linear program with HiGHS / CBC / GLPK fallback.                                |
| `requirements.txt` | Pinned: `fastapi==0.111.0`, `uvicorn[standard]==0.30.1`, `pydantic>=2.6,<3`, `pulp==2.7.0`, `httpx==0.27.0`. |
| `sample_input.json`| 24-hour reference scenario with two notes (one actionable, one off-topic).               |
| `Dockerfile`       | Production image, non-root, healthcheck, CBC preinstalled.                              |
| `.gitignore`       | Blocks `.env`, `.venv`, `__pycache__`, IDE state, OS metadata.                            |

---

## Security

**No secrets are baked into the source code, the Docker image, or the
`.gitignore`-tracked configuration files.** All credentials (`LLM_API_KEY`,
`OPENAI_API_KEY`, custom base URLs) are read from **runtime environment
variables only**.

- The Dockerfile never accepts `ARG`s for secrets.
- The Dockerfile never performs `ENV LLM_API_KEY=…`.
- The `.gitignore` blocks `.env`, `*.env`, `api_keys.*`, and `secrets.*` files.
- The `/optimize-energy` endpoint never echoes the API key or any request header.

---

## Troubleshooting

| Symptom                                                              | Likely cause / fix                                                                                          |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `uvicorn: command not recognized`                                    | Use `python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload`.                                       |
| Server starts, `/optimize-energy` returns `401`                      | Your key does not match the provider you're sending to. Use the Groq / OpenRouter auto-detect, or set `LLM_BASE_URL` explicitly. |
| Server returns `404 model_not_found`                                 | The model name is wrong for your provider. Run `curl` against `{LLM_BASE_URL}/models` and set `LLM_MODEL`. |
| Server returns `500` about a missing solver                          | Local Python lacks CBC. The bundled `HiGHS_CMD` should be used; if your PuLP is older, run in Docker.     |
| Output looks naïve / wrong directive types                           | The LLM isn't following the system prompt. Try a larger model, or check `LLM_BASE_URL` points at it.        |
| Changes to env vars have no effect                                   | `uvicorn --reload` watches source files, not env vars. Stop with `Ctrl+C` and restart.                     |
| `pip install` fails on Windows building wheels                       | Use the prebuilt wheels: `pip install --only-binary=:all: -r requirements.txt`.                            |

---

**BUP CSE Fest 2026 — GridWise Track.** Built with FastAPI, Pydantic v2, PuLP,
and a generous amount of coffee.

---

## Live Demo (no install required)

A static showcase site lives under `docs/` and is published via **GitHub Pages**.
It calls the deployed FastAPI service in real time.

- **Demo site**: <https://hurairiam.github.io/BUP_hackathon_prelims/>
- **Live API**: <https://cogitator-optimus.onrender.com>
- **Swagger**: <https://cogitator-optimus.onrender.com/docs>

The site is fully self-contained — judges can type operator notes, click
**Optimize**, and see the real Pydantic-validated + PuLP-solved 24-hour plan
without installing anything. CORS is locked down to the GitHub Pages origin.
