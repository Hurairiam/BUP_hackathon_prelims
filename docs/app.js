/* ---------------------------------------------------------------------------
 * Cogitator-Optimus — Live Demo (browser)
 * Builds a ScenarioRequest from the form, POSTs to the FastAPI service,
 * renders the OptimizeResponse.
 * --------------------------------------------------------------------------- */

const SAMPLE = {
  scenario_id: "SAMPLE-01",
  battery_capacity_kwh: 220.0,
  initial_energy_kwh: 110.0,
  min_energy_kwh: 40.0,
  max_charge_kwh_per_hour: 50.0,
  max_discharge_kwh_per_hour: 50.0,
  demand_kwh: [42,38,35,33,34,40,55,72,88,95,100,105,108,110,112,115,118,122,128,130,120,95,70,55],
  solar_kwh:  [0,0,0,0,0,0,5,20,45,70,90,105,115,110,95,75,50,25,10,2,0,0,0,0],
  tariff_bdt_per_kwh: [8.5,8,7.5,7.5,7.5,8,9,10.5,11,11,10.5,10,10,10.5,11,11.5,12,13,14,14.5,13,11,9.5,9],
  operator_notes: []
};

const $ = (id) => document.getElementById(id);

function buildScenario() {
  const notes = $("notes").value
    .split("\n").map(s => s.trim()).filter(Boolean).slice(0, 3);

  return {
    ...SAMPLE,
    scenario_id: "DEMO-" + Date.now().toString(36).toUpperCase(),
    battery_capacity_kwh: parseFloat($("b-cap").value) || 220,
    initial_energy_kwh:    parseFloat($("b-init").value) || 110,
    min_energy_kwh:        parseFloat($("b-min").value)  || 40,
    max_charge_kwh_per_hour:    parseFloat($("b-ch").value)  || 50,
    max_discharge_kwh_per_hour: parseFloat($("b-dch").value) || 50,
    operator_notes: notes
  };
}

async function runOptimization() {
  const btn = $("run");
  const status = $("status");
  btn.disabled = true;
  status.className = "status";
  status.textContent = "Calling API…";

  const apiBase = $("api-base").value.replace(/\/+$/, "");
  const url = apiBase + "/optimize-energy";
  $("api-url").textContent = url;

  const scenario = buildScenario();

  const headers = { "Content-Type": "application/json" };
  const userKey = $("llm-key").value.trim();
  if (userKey) headers["X-LLM-Key"] = userKey;  // server ignores if it already has its own key

  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(scenario)
    });
  } catch (err) {
    status.className = "status err";
    status.textContent = "Network error: " + err.message;
    btn.disabled = false;
    return;
  }

  const text = await res.text();
  let data;
  try { data = JSON.parse(text); }
  catch { data = { detail: text }; }

  if (!res.ok) {
    status.className = "status err";
    status.textContent = `HTTP ${res.status}`;
    renderError(data);
    btn.disabled = false;
    return;
  }

  status.className = "status ok";
  status.textContent = "✓ 200 OK";
  renderPlan(data);
  btn.disabled = false;
}

function renderError(err) {
  $("summary").innerHTML =
    `<div class="tile" style="grid-column:1/-1;border-color:#ff5050;">
       <div class="label">Error</div>
       <div class="value" style="font-size:14px;color:#ff8a8a;">${escapeHtml(JSON.stringify(err, null, 2))}</div>
     </div>`;
  $("directives").innerHTML = "";
  $("plan-body").innerHTML = "";
}

function renderPlan(data) {
  // Summary tiles
  const tiles = [
    ["scenario",      data.scenario_id || "—"],
    ["total grid",    num(data.total_grid_kwh) + " kWh"],
    ["total cost",    num(data.total_cost_bdt) + " BDT"],
    ["peak grid",     num(data.peak_grid_kwh) + " kWh"]
  ];
  $("summary").innerHTML = tiles.map(([l, v]) =>
    `<div class="tile"><div class="label">${l}</div><div class="value">${escapeHtml(String(v))}</div></div>`
  ).join("");

  // Directives
  const dirs = data.directive_interpretation || [];
  if (!dirs.length) {
    $("directives").innerHTML = `<div class="directive"><p>No directives parsed.</p></div>`;
  } else {
    $("directives").innerHTML = dirs.map(d => {
      const isNoOp = d.directive_type === "no_op";
      const adj = d.structured_adjustment
        ? `<code>${escapeHtml(JSON.stringify(d.structured_adjustment))}</code>`
        : "";
      return `
        <div class="directive">
          <h4>
            <span class="tag ${isNoOp ? "noop" : ""}">${escapeHtml(d.directive_type)}</span>
            <span style="color:#888;font-weight:400;">note #${d.note_index}</span>
          </h4>
          <p>${escapeHtml(d.note_text || "")}</p>
          ${d.reasoning ? `<p style="color:#bbb;">↳ ${escapeHtml(d.reasoning)}</p>` : ""}
          ${adj ? `<p>${adj}</p>` : ""}
        </div>`;
    }).join("");
  }

  // Hourly plan
  const rows = (data.hourly_plan || []).map(h => `
    <tr>
      <td>${h.hour}</td>
      <td>${num(h.demand_kwh)}</td>
      <td>${num(h.solar_used_kwh)}</td>
      <td><strong style="color:#ff8a8a;">${num(h.grid_kwh)}</strong></td>
      <td>${num(h.charge_kwh)}</td>
      <td>${num(h.discharge_kwh)}</td>
      <td>${num(h.battery_energy_after_kwh)}</td>
      <td><span class="action ${h.battery_action}">${escapeHtml(h.battery_action)}</span></td>
      <td>${num(h.tariff_bdt_per_kwh)}</td>
    </tr>
  `).join("");
  $("plan-body").innerHTML = rows;
}

function num(x) {
  if (x === null || x === undefined) return "—";
  const n = Number(x);
  if (!Number.isFinite(n)) return String(x);
  return n.toFixed(2).replace(/\.?0+$/, "") || "0";
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function syncApiUrl() {
  const base = $("api-base").value.replace(/\/+$/, "");
  $("api-url").textContent = base + "/optimize-energy";
}

document.addEventListener("DOMContentLoaded", () => {
  $("run").addEventListener("click", runOptimization);
  $("reset").addEventListener("click", () => {
    $("notes").value = SAMPLE.operator_notes.join("\n") ||
      "Wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.\nThe sports office moved next month's registration deadline.";
    $("summary").innerHTML = "";
    $("directives").innerHTML = "";
    $("plan-body").innerHTML = "";
    $("status").textContent = "";
  });
  $("copy-url").addEventListener("click", () => {
    navigator.clipboard.writeText($("api-url").textContent).then(() => {
      $("copy-url").textContent = "Copied!";
      setTimeout(() => $("copy-url").textContent = "Copy", 1500);
    });
  });
  $("api-base").addEventListener("input", syncApiUrl);
  syncApiUrl();
});
