"""
FastAPI application for the GridWise Smart Campus Energy Optimization
Challenge (BUP CSE Fest 2026 Hackathon).

Endpoints:
    GET  /health        -> {"status": "ok"}
    POST /optimize-energy -> runs the full LLM -> Pydantic -> PuLP pipeline.

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from schemas import (
    DirectiveInterpretation,
    ExtractedDirective,
    OptimizeResponse,
    ScenarioRequest,
    StructuredAdjustment,
)
from solver import recompute_totals, solve_energy_schedule

from fastapi.middleware.cors import CORSMiddleware  # added for public showcase


# --------------------------------------------------------------------------- #
# Logging & app setup
# --------------------------------------------------------------------------- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cogitator_optimus")

app = FastAPI(
    title="Cogitator-Optimus",
    version="1.0.0",
    description=(
        "Smart Campus Energy Optimization — LLM-interpreted 24-hour energy "
        "scheduler for BUP CSE Fest 2026."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Allow the GitHub Pages showcase (and localhost dev) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hurairiam.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-LLM-Key"],
    max_age=600,
)


# --------------------------------------------------------------------------- #
# Branding: black + red Swagger / ReDoc theme
# --------------------------------------------------------------------------- #
_BRAND_CSS = """
<style>
  :root {
    --co-bg:        #0a0a0a;
    --co-bg-2:      #141414;
    --co-panel:     #1a1a1a;
    --co-border:    #2a2a2a;
    --co-text:      #e8e8e8;
    --co-muted:     #8a8a8a;
    --co-red:       #d10000;
    --co-red-2:     #ff1f1f;
    --co-red-soft:  rgba(209, 0, 0, 0.18);
  }
  html, body { background: var(--co-bg) !important; color: var(--co-text) !important; }
  .swagger-ui { background: var(--co-bg) !important; }
  .swagger-ui .topbar { background: var(--co-bg-2) !important; border-bottom: 1px solid var(--co-border) !important; }
  .swagger-ui .topbar .download-url-wrapper .select-label,
  .swagger-ui .topbar .download-url-wrapper input,
  .swagger-ui .topbar .download-url-wrapper .download-url-button { display: none !important; }
  .swagger-ui .info { color: var(--co-text) !important; }
  .swagger-ui .info .title { color: var(--co-red-2) !important; letter-spacing: 1px; }
  .swagger-ui .info .description, .swagger-ui .info p, .swagger-ui .info li { color: var(--co-muted) !important; }
  .swagger-ui .scheme-container { background: var(--co-bg-2) !important; box-shadow: none !important; padding: 12px 0; }
  .swagger-ui .opblock-tag { color: var(--co-red-2) !important; border-bottom: 1px solid var(--co-border) !important; }
  .swagger-ui .opblock { background: var(--co-panel) !important; border: 1px solid var(--co-border) !important; border-radius: 6px !important; box-shadow: none !important; }
  .swagger-ui .opblock .opblock-summary { background: transparent !important; }
  .swagger-ui .opblock .opblock-summary-method-text { color: #fff !important; }
  .swagger-ui .opblock.opblock-get .opblock-summary-method { background: var(--co-red) !important; }
  .swagger-ui .opblock.opblock-post .opblock-summary-method { background: var(--co-red-2) !important; }
  .swagger-ui .opblock .opblock-section-header { background: var(--co-bg-2) !important; }
  .swagger-ui .opblock-body pre, .swagger-ui .opblock-body code, .swagger-ui .microlight { color: #f5f5f5 !important; background: #0f0f0f !important; }
  .swagger-ui .btn { background: var(--co-red) !important; color: #fff !important; border: 1px solid var(--co-red) !important; }
  .swagger-ui .btn:hover { background: var(--co-red-2) !important; }
  .swagger-ui .btn.cancel { background: transparent !important; color: var(--co-red-2) !important; border-color: var(--co-red) !important; }
  .swagger-ui input, .swagger-ui textarea, .swagger-ui select { background: #0f0f0f !important; color: var(--co-text) !important; border: 1px solid var(--co-border) !important; }
  .swagger-ui table thead tr th, .swagger-ui table tbody tr td { color: var(--co-text) !important; border-color: var(--co-border) !important; }
  .swagger-ui .response-col_status { color: var(--co-red-2) !important; }
  .swagger-ui .response-col_description { color: var(--co-muted) !important; }
  .swagger-ui .highlight-code { background: #0f0f0f !important; }
  .swagger-ui section.models { border: 1px solid var(--co-border) !important; }
  .swagger-ui section.models .model-container { background: var(--co-panel) !important; }
  .swagger-ui .model-toggle { color: var(--co-red-2) !important; }
  /* ReDoc overrides */
  body { background: var(--co-bg) !important; }
  redoc { background: var(--co-bg) !important; }
  .redoc-wrap { background: var(--co-bg) !important; }
  .menu-content, .api-content { background: var(--co-bg) !important; color: var(--co-text) !important; }
  .api-info h1, .api-info h2, .api-info h3, .operation-type { color: var(--co-red-2) !important; }
  .sc-jTzLTM, .sc-gqjmRU, .sc-iwsKbI, .sc-bdVaJa, .sc-htpNat { color: var(--co-text) !important; }
</style>
"""

_BRAND_JS = """
<script>
  // Add a Cogitator-Optimus badge to the Swagger topbar
  (function () {
    const mount = () => {
      const top = document.querySelector('.swagger-ui .topbar');
      if (!top || top.dataset.coBranded) return;
      top.dataset.coBranded = "1";
      const wrap = document.createElement('div');
      wrap.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 16px;";
      wrap.innerHTML =
        '<span style="font-family:Consolas,monospace;font-size:13px;' +
        'background:linear-gradient(90deg,#d10000,#ff1f1f);' +
        'color:#fff;padding:4px 10px;border-radius:4px;letter-spacing:1.5px;' +
        'box-shadow:0 0 12px rgba(255,31,31,0.45);">' +
        'COGITATOR-OPTIMUS</span>' +
        '<span style="color:#8a8a8a;font-size:12px;letter-spacing:1px;">' +
        'SMART CAMPUS ENERGY OPTIMIZATION</span>';
      top.prepend(wrap);
    };
    if (document.readyState === 'complete') mount();
    else window.addEventListener('load', mount);
    setTimeout(mount, 200);
  })();
</script>
"""


def _inject_branding() -> "HTMLResponse":  # type: ignore[name-defined]
    """Serve Swagger UI / ReDoc HTML with the Cogitator-Optimus black+red theme."""

    from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
    from fastapi.responses import HTMLResponse

    endpoint = getattr(_inject_branding, "_path", "/docs")
    if endpoint == "/redoc":
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} — API Reference",
        )

    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} — API",
    )


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> "HTMLResponse":  # type: ignore[name-defined]
    """Black + red Swagger UI for Cogitator-Optimus."""

    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} — API",
    ).body.decode("utf-8")
    html = html.replace("</head>", _BRAND_CSS + _BRAND_JS + "</head>")
    return HTMLResponse(content=html, status_code=200)


@app.get("/redoc", include_in_schema=False)
async def custom_redoc() -> "HTMLResponse":  # type: ignore[name-defined]
    """Black + red ReDoc reference for Cogitator-Optimus."""

    from fastapi.openapi.docs import get_redoc_html
    from fastapi.responses import HTMLResponse

    html = get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} — API Reference",
    ).body.decode("utf-8")
    html = html.replace("</head>", _BRAND_CSS + "</head>")
    return HTMLResponse(content=html, status_code=200)


# --------------------------------------------------------------------------- #
# Landing page
# --------------------------------------------------------------------------- #
_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Cogitator-Optimus — Smart Campus Energy Optimization</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #0a0a0a; color: #e8e8e8;
    font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
    min-height: 100vh; }
  body { background:
    radial-gradient(1200px 800px at 80% -10%, rgba(209,0,0,0.18), transparent 60%),
    radial-gradient(900px 600px at -10% 110%, rgba(255,31,31,0.12), transparent 60%),
    #0a0a0a; }
  header { padding: 28px 48px; display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid #1a1a1a; }
  .brand { display: flex; align-items: center; gap: 14px;
    font-family: Consolas, "Courier New", monospace; letter-spacing: 3px; font-size: 14px; }
  .logo { width: 34px; height: 34px; border-radius: 8px;
    background: linear-gradient(135deg, #d10000, #ff1f1f);
    display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 16px;
    box-shadow: 0 0 18px rgba(255, 31, 31, 0.5); }
  .brand small { color: #8a8a8a; letter-spacing: 2px; font-size: 10px; }
  nav a { color: #e8e8e8; text-decoration: none; margin-left: 22px; font-size: 14px;
    padding: 8px 14px; border-radius: 6px; }
  nav a.primary { background: linear-gradient(135deg, #d10000, #ff1f1f);
    color: #fff; box-shadow: 0 0 14px rgba(255,31,31,0.4); }
  nav a:hover { color: #ff1f1f; }
  main { padding: 80px 48px; max-width: 1100px; margin: 0 auto; }
  .eyebrow { color: #ff1f1f; letter-spacing: 4px; font-size: 12px;
    font-family: Consolas, monospace; margin-bottom: 18px; }
  h1 { font-size: clamp(40px, 6vw, 72px); margin: 0 0 18px 0; line-height: 1.05;
    font-weight: 800; letter-spacing: -1px; }
  h1 span { background: linear-gradient(90deg, #ff1f1f, #ff7676);
    -webkit-background-clip: text; background-clip: text; color: transparent; }
  .lede { color: #b8b8b8; font-size: 18px; line-height: 1.6; max-width: 720px; }
  .cta { margin-top: 36px; display: flex; flex-wrap: wrap; gap: 14px; }
  .cta a { text-decoration: none; padding: 14px 22px; border-radius: 8px;
    font-weight: 600; font-size: 15px; }
  .cta .primary { background: linear-gradient(135deg, #d10000, #ff1f1f); color: #fff;
    box-shadow: 0 0 24px rgba(255,31,31,0.35); }
  .cta .ghost { border: 1px solid #2a2a2a; color: #e8e8e8; }
  .cta .ghost:hover { border-color: #ff1f1f; color: #ff1f1f; }
  .grid { margin-top: 80px; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 18px; }
  .card { background: linear-gradient(180deg, #141414, #0f0f0f);
    border: 1px solid #1f1f1f; border-radius: 12px; padding: 22px; transition: 0.2s; }
  .card:hover { border-color: #d10000; transform: translateY(-2px); }
  .card h3 { margin: 8px 0 8px 0; font-size: 17px; color: #fff; }
  .card p { color: #8a8a8a; font-size: 14px; line-height: 1.55; margin: 0; }
  .card .tag { display: inline-block; font-family: Consolas, monospace; font-size: 11px;
    color: #ff1f1f; background: rgba(209,0,0,0.12); padding: 3px 8px; border-radius: 4px;
    letter-spacing: 1.5px; }
  footer { margin-top: 90px; padding: 30px 48px; border-top: 1px solid #1a1a1a;
    color: #6a6a6a; font-size: 12px; display: flex; justify-content: space-between;
    flex-wrap: wrap; gap: 12px; font-family: Consolas, monospace; letter-spacing: 1.5px; }
  footer span { color: #d10000; }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo">CO</div>
      <div>
        <div>COGITATOR-OPTIMUS</div>
        <small>SMART CAMPUS ENERGY OPTIMIZATION</small>
      </div>
    </div>
    <nav>
      <a href="/health">/health</a>
      <a href="/docs" class="primary">Launch Console →</a>
    </nav>
  </header>

  <main>
    <div class="eyebrow">// BUP CSE FEST 2026 — HACKATHON SUBMISSION</div>
    <h1>Grid decisions,<br/>spoken in <span>natural language</span>.</h1>
    <p class="lede">
      Cogitator-Optimus turns free-form operator notes into a minimum-cost
      24-hour energy schedule. Free-form text in. Strictly validated
      directives. PuLP-optimized dispatch out — every kilowatt-hour accounted for.
    </p>
    <div class="cta">
      <a href="/docs" class="primary">Open API Console</a>
      <a href="/redoc" class="ghost">Read API Reference</a>
      <a href="/optimize-energy" class="ghost" title="Returns 405 — POST only">POST /optimize-energy</a>
    </div>

    <div class="grid">
      <div class="card">
        <span class="tag">01 · LLM</span>
        <h3>Operator notes → structured directives</h3>
        <p>Groq-compatible chat-completions interface parses 1–3 free-form notes
          into a typed directive array, ready for validation.</p>
      </div>
      <div class="card">
        <span class="tag">02 · PYDANTIC</span>
        <h3>Auto-healing guardrails</h3>
        <p>Hours are deduplicated and clamped. Solar factors clamped to [0, 1].
          No-ops are enforced. Anything malformed becomes a valid no_op.</p>
      </div>
      <div class="card">
        <span class="tag">03 · PULP</span>
        <h3>24-hour minimum-cost LP</h3>
        <p>HiGHS-powered linear program balances supply, demand, battery SoC,
          rate limits and end-of-day neutrality — with directive overlays.</p>
      </div>
      <div class="card">
        <span class="tag">04 · SAFETY</span>
        <h3>Top-level catch-all</h3>
        <p>Every request is wrapped in a try/except. The API returns a clean
          500 with diagnostics — and the process never crashes.</p>
      </div>
    </div>
  </main>

  <footer>
    <div>BUP CSE FEST <span>2026</span> · GRIDWISE TRACK</div>
    <div>COGITATOR-OPTIMUS v1.0.0 · FASTAPI + PYDANTIC + PULP</div>
  </footer>
</body>
</html>
"""


@app.get("/", include_in_schema=False)
async def landing_page() -> "HTMLResponse":  # type: ignore[name-defined]
    """Black-and-red landing page for Cogitator-Optimus."""

    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=_LANDING_HTML, status_code=200)


# --------------------------------------------------------------------------- #
# LLM configuration (OpenAI-compatible)
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# LLM configuration (OpenAI-compatible)
# --------------------------------------------------------------------------- #
# Provider auto-detection:
#   * If LLM_BASE_URL is set, honor it (any OpenAI-compatible endpoint).
#   * Otherwise, infer from the key prefix:
#       - "gsk_"          -> Groq      (https://api.groq.com/openai/v1)
#       - "sk-or-"        -> OpenRouter (https://openrouter.ai/api/v1)
#       - "sk-" (default) -> OpenAI    (https://api.openai.com/v1)
#   * Override model via LLM_MODEL; sensible defaults per provider.
# --------------------------------------------------------------------------- #
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
_LLM_BASE_URL_ENV = os.getenv("LLM_BASE_URL")
_LLM_MODEL_ENV = os.getenv("LLM_MODEL")


def _infer_provider() -> tuple[str, str]:
    """Return (base_url, default_model) inferred from the API key shape."""

    key = (LLM_API_KEY or "").strip()
    if _LLM_BASE_URL_ENV:
        # Honour explicit override.
        return _LLM_BASE_URL_ENV.rstrip("/"), (_LLM_MODEL_ENV or "gpt-4o-mini")
    if key.startswith("gsk_"):
        return "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"
    if key.startswith("sk-or-"):
        return "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"
    return "https://api.openai.com/v1", "gpt-4o-mini"


LLM_BASE_URL, LLM_DEFAULT_MODEL = _infer_provider()
LLM_MODEL = _LLM_MODEL_ENV or LLM_DEFAULT_MODEL
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

# Only OpenAI natively supports the strict json_object response_format flag.
_LLM_SUPPORTS_JSON_MODE = "api.openai.com" in LLM_BASE_URL


SYSTEM_PROMPT = """You are an energy grid operator assistant. Analyze the user's 1-3 notes.
For each note, output exactly one JSON interpretation in the exact order of the notes (index 0, 1, 2).

Match each note to ONE of these directive_types:

1. solar_reduction: Requires {"hours": [int], "factor": float}.
   (Factor is the REMAINING usable solar. 80% reduction = 0.2 factor. 50% reduction = 0.5 factor.)
2. minimum_battery_reserve: Requires {"hours": [int], "minimum_energy_kwh": float}.
   (Convert any relative percentage to absolute kWh based on battery capacity.)
3. no_charge_window: Requires {"hours": [int]}.
4. no_discharge_window: Requires {"hours": [int]}.
5. max_grid_window: Requires {"hours": [int], "max_grid_kwh": float}.
6. no_op: Use this if the note does not affect the energy schedule.
   structured_adjustment must be null for no_op.

Hours must be unique integers 0-23. Windows are start-inclusive, end-exclusive.
"1 PM to 3 PM" means [13, 14]. "from 22:00 to 06:00" means [22, 23, 0, 1, 2, 3, 4, 5].
"noon to 4pm" means [12, 13, 14, 15].

Return ONLY a JSON array (no prose, no markdown fences). Each element must have:
  - note_index: int
  - note_text: string (echo of the input note)
  - directive_type: one of the 6 strings above
  - applies: boolean
  - structured_adjustment: object or null
  - reasoning: short string explaining the interpretation
"""


# --------------------------------------------------------------------------- #
# LLM client (OpenAI-compatible chat completions)
# --------------------------------------------------------------------------- #
async def _call_llm(messages: List[Dict[str, str]]) -> str:
    """Call the OpenAI-compatible /chat/completions endpoint.

    Returns the raw assistant text content. Raises RuntimeError if the call
    fails or if no API key is configured.
    """

    if not LLM_API_KEY:
        raise RuntimeError(
            "LLM_API_KEY (or OPENAI_API_KEY) environment variable is not set."
        )

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.0,
    }
    if _LLM_SUPPORTS_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {data}") from exc


# --------------------------------------------------------------------------- #
# JSON extraction from LLM output
# --------------------------------------------------------------------------- #
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", re.IGNORECASE)


def _extract_json_array(raw: str) -> List[Any]:
    """Robustly extract a JSON array from an LLM reply.

    Strategy:
        1. Look for ```json ... ``` fenced blocks.
        2. Look for a balanced array starting with '['.
        3. If the model returned a single object, wrap it in a list.
    """

    if not raw or not raw.strip():
        raise ValueError("LLM returned empty content.")

    text = raw.strip()

    # 1) Fenced JSON.
    fence_match = _JSON_FENCE_RE.search(text)
    candidate = fence_match.group(1) if fence_match else text

    # 2) Try direct parse first.
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # 3) Find the first '[' or '{' and walk to a balanced close.
    start = min((i for i, ch in enumerate(candidate) if ch in "[{"), default=-1)
    if start == -1:
        raise ValueError(f"No JSON object/array found in LLM output: {text[:200]}")

    opener = candidate[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        raise ValueError(f"Could not balance JSON in LLM output: {text[:200]}")

    blob = candidate[start:end]
    parsed = json.loads(blob)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("LLM JSON was neither object nor array.")


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
async def _extract_directives_with_llm(
    notes: List[str], scenario: ScenarioRequest
) -> List[ExtractedDirective]:
    """Step A + Step B of the pipeline."""

    # Inject battery capacity so the model can translate "% reserve" to kWh.
    user_payload = {
        "battery_capacity_kwh": scenario.battery_capacity_kwh,
        "min_energy_kwh": scenario.min_energy_kwh,
        "operator_notes": notes,
    }
    user_msg = (
        "Scenario context:\n"
        + json.dumps(user_payload)
        + "\n\nReturn a JSON array with one entry per note, in order."
    )

    raw = await _call_llm(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
    )

    items = _extract_json_array(raw)

    # Auto-heal & coerce via Pydantic. We never raise on a single bad item:
    # we coerce and continue, but if EVERY item is unusable we raise.
    directives: List[ExtractedDirective] = []
    for idx, original_text in enumerate(notes):
        item: Optional[Dict[str, Any]] = None
        if idx < len(items) and isinstance(items[idx], dict):
            item = items[idx]
        else:
            # Try to find an entry whose note_index matches.
            for it in items:
                if isinstance(it, dict) and int(it.get("note_index", -1)) == idx:
                    item = it
                    break

        if item is None:
            # Fall back to a defensive no_op.
            item = {
                "note_index": idx,
                "note_text": original_text,
                "directive_type": "no_op",
                "applies": False,
                "structured_adjustment": None,
                "reasoning": "LLM did not return a parseable entry; defaulted to no_op.",
            }

        # Ensure minimum keys before Pydantic sees them.
        item.setdefault("note_index", idx)
        item.setdefault("note_text", original_text)
        item.setdefault("directive_type", "no_op")
        item.setdefault("applies", True)
        item.setdefault("structured_adjustment", None)

        try:
            directive = ExtractedDirective.model_validate(item)
        except ValidationError as exc:
            logger.warning("Directive %d failed validation: %s; coercing to no_op.", idx, exc)
            directive = ExtractedDirective(
                note_index=idx,
                note_text=original_text,
                directive_type="no_op",
                applies=False,
                structured_adjustment=None,
                reasoning=f"Auto-healed: validation error: {exc.errors()[0]['msg']}",
            )
        directives.append(directive)

    if not directives:
        raise RuntimeError("LLM produced no usable directives.")

    return directives


def _build_summary(
    scenario: ScenarioRequest,
    directives: List[ExtractedDirective],
    result,
) -> str:
    """Short human-readable summary of the plan."""

    applied = [d for d in directives if d.applies and d.directive_type != "no_op"]
    parts: List[str] = [
        f"Optimized 24h schedule for scenario {scenario.scenario_id}.",
        f"Total grid draw: {result.total_grid_kwh:.2f} kWh.",
        f"Total cost: {result.total_cost_bdt:.2f} BDT.",
        f"Peak grid: {result.peak_grid_kwh:.2f} kWh.",
    ]
    if applied:
        bits = ", ".join(d.directive_type.value for d in applied)
        parts.append(f"Applied directives: {bits}.")
    else:
        parts.append("No operator directives applied.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> Dict[str, str]:
    """Lightweight liveness probe."""

    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(payload: ScenarioRequest) -> OptimizeResponse:
    """Full pipeline: LLM -> Pydantic -> PuLP -> response."""

    try:
        # Step A: LLM extraction.
        notes = list(payload.operator_notes)
        directives = await _extract_directives_with_llm(notes, payload)

        # Step B has already happened inside _extract_directives_with_llm
        # via the ExtractedDirective validators.

        # Steps C + D: solver applies validated directives and solves.
        result = solve_energy_schedule(payload, directives)

        # Step E: format response with independently recalculated totals.
        total_grid, total_cost, peak_grid = recompute_totals(result.hourly_plan)

        interpretations: List[DirectiveInterpretation] = [
            DirectiveInterpretation(
                note_index=d.note_index,
                note_text=d.note_text,
                directive_type=d.directive_type,
                applies=d.applies,
                reasoning=d.reasoning,
                structured_adjustment=d.structured_adjustment,
            )
            for d in directives
        ]

        return OptimizeResponse(
            scenario_id=payload.scenario_id,
            directive_interpretation=interpretations,
            hourly_plan=result.hourly_plan,
            total_grid_kwh=total_grid,
            total_cost_bdt=total_cost,
            peak_grid_kwh=peak_grid,
            plan_summary=_build_summary(payload, directives, result),
        )

    except HTTPException:
        # Re-raise explicit HTTP errors as-is.
        raise
    except (ValidationError, ValueError) as exc:
        logger.exception("Validation/payload error in /optimize-energy")
        raise HTTPException(status_code=400, detail=f"Invalid input: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - intentional safety net
        logger.exception("Unhandled error in /optimize-energy")
        raise HTTPException(
            status_code=500,
            detail=f"Optimization failed: {exc}",
        ) from exc


# --------------------------------------------------------------------------- #
# Local dev entrypoint
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
