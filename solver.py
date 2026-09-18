"""
PuLP-based 24-hour linear-programming solver for the GridWise smart-campus
energy scheduler.

Module entry: `solve_energy_schedule(scenario, directives) -> OptimizationResult`

Decision variables per hour (h = 0..23):
    grid_kwh[h]               Continuous >= 0
    solar_used_kwh[h]         Continuous >= 0
    charge_kwh[h]             Continuous >= 0
    discharge_kwh[h]          Continuous >= 0
    battery_energy_after_kwh[h] Continuous >= 0

Objective:
    Minimize sum(grid_kwh[h] * tariff_bdt_per_kwh[h])

Base physical constraints:
    1. Energy balance:      grid + solar_used + discharge == demand + charge
    2. Solar limit:         solar_used[h] <= effective_solar_kwh[h]
    3. Battery transition:  SoC[h] = SoC[h-1] + charge - discharge
    4. Battery bounds:      min_energy_kwh <= SoC[h] <= capacity
    5. Rate limits:         charge/discharge <= per-hour maxima
    6. End-of-day:          SoC[23] == initial_energy_kwh

Dynamic constraints (driven by validated operator directives):
    - solar_reduction:           effective_solar[h] = solar[h] * factor
    - minimum_battery_reserve:   SoC[h] >= directive.minimum_energy_kwh
    - no_charge_window:          charge[h] == 0
    - no_discharge_window:       discharge[h] == 0
    - max_grid_window:           grid[h] <= max_grid_kwh
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import pulp

from schemas import (
    BatteryAction,
    DirectiveType,
    ExtractedDirective,
    HourlyPlanEntry,
    ScenarioRequest,
)


# --------------------------------------------------------------------------- #
# Small tolerance constants
# --------------------------------------------------------------------------- #
EPS = 1e-6          # used to zero-out tiny numerical noise in results
ACTION_EPS = 0.01   # below this, a battery flow is treated as "idle"
ROUND_HOUR = 4      # per-hour decimals in the response
ROUND_TOTAL = 2     # total/summary decimals in the response


@dataclass
class OptimizationResult:
    """Container for the solved hourly plan and aggregated totals."""

    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _select_hour_set(directives: List[ExtractedDirective], directive_type: DirectiveType) -> List[int]:
    """Collect, deduplicate, and sort all hour indices referenced by a directive type."""

    hours: List[int] = []
    for d in directives:
        if d.directive_type != directive_type or not d.applies:
            continue
        adj = d.structured_adjustment
        if adj and adj.hours:
            hours.extend(adj.hours)
    return sorted(set(h for h in hours if 0 <= h <= 23))


def _directive_value(
    directives: List[ExtractedDirective],
    directive_type: DirectiveType,
    hour: int,
    attr: str,
) -> float | None:
    """Return the first matching scalar value from a directive's structured_adjustment."""

    for d in directives:
        if d.directive_type != directive_type or not d.applies:
            continue
        adj = d.structured_adjustment
        if not adj or not adj.hours or hour not in adj.hours:
            continue
        val = getattr(adj, attr, None)
        if val is not None:
            return float(val)
    return None


# --------------------------------------------------------------------------- #
# Main solver
# --------------------------------------------------------------------------- #
def solve_energy_schedule(
    request_data: ScenarioRequest,
    validated_directives: List[ExtractedDirective],
) -> OptimizationResult:
    """Solve the 24-hour energy schedule with PuLP.

    Raises RuntimeError if the LP fails to find an optimal solution.
    """

    hours = list(range(24))

    # ----------------------------------------------------------------- data
    demand = list(request_data.demand_kwh)
    base_solar = list(request_data.solar_kwh)
    tariff = list(request_data.tariff_bdt_per_kwh)

    capacity = float(request_data.battery_capacity_kwh)
    initial_soc = float(request_data.initial_energy_kwh)
    min_soc = float(request_data.min_energy_kwh)
    max_charge = float(request_data.max_charge_kwh_per_hour)
    max_discharge = float(request_data.max_discharge_kwh_per_hour)

    # ----------------------------------------------------- effective solar
    # solar_reduction directives scale the *base* solar for their hours.
    effective_solar = list(base_solar)
    for h in hours:
        factor = _directive_value(validated_directives, DirectiveType.SOLAR_REDUCTION, h, "factor")
        if factor is not None:
            effective_solar[h] = base_solar[h] * factor

    # ------------------------------------------------- per-hour lock hours
    no_charge_hours = set(_select_hour_set(validated_directives, DirectiveType.NO_CHARGE_WINDOW))
    no_discharge_hours = set(_select_hour_set(validated_directives, DirectiveType.NO_DISCHARGE_WINDOW))
    min_reserve_hours = set(
        _select_hour_set(validated_directives, DirectiveType.MINIMUM_BATTERY_RESERVE)
    )
    max_grid_hours = set(_select_hour_set(validated_directives, DirectiveType.MAX_GRID_WINDOW))

    # ---------------------------------------------------------- LP problem
    prob = pulp.LpProblem("GridWiseSmartCampus", pulp.LpMinimize)

    grid = pulp.LpVariable.dicts("grid", hours, lowBound=0)
    solar_used = pulp.LpVariable.dicts("solar_used", hours, lowBound=0)
    charge = pulp.LpVariable.dicts("charge", hours, lowBound=0)
    discharge = pulp.LpVariable.dicts("discharge", hours, lowBound=0)
    soc = pulp.LpVariable.dicts("soc", hours, lowBound=0)

    # -------------------------------------------------------- objective fn
    prob += pulp.lpSum(grid[h] * tariff[h] for h in hours), "TotalCost"

    # ------------------------------------------------- base constraints
    # 1) Energy balance
    for h in hours:
        prob += (
            grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h],
            f"balance_{h}",
        )

    # 2) Solar limit
    for h in hours:
        prob += solar_used[h] <= effective_solar[h], f"solar_limit_{h}"

    # 3) Battery transition
    prob += soc[0] == initial_soc + charge[0] - discharge[0], "soc_init"
    for h in hours[1:]:
        prob += (
            soc[h] == soc[h - 1] + charge[h] - discharge[h],
            f"soc_transition_{h}",
        )

    # 4) Battery bounds (upper) + dynamic per-hour floor
    for h in hours:
        prob += soc[h] <= capacity, f"soc_upper_{h}"
        prob += soc[h] >= min_soc, f"soc_lower_base_{h}"

    # 5) Rate limits (and dynamic window locks)
    for h in hours:
        if h in no_charge_hours:
            prob += charge[h] == 0, f"no_charge_{h}"
        else:
            prob += charge[h] <= max_charge, f"charge_rate_{h}"

        if h in no_discharge_hours:
            prob += discharge[h] == 0, f"no_discharge_{h}"
        else:
            prob += discharge[h] <= max_discharge, f"discharge_rate_{h}"

    # Dynamic per-hour minimum reserve (overrides base floor if higher).
    for h in min_reserve_hours:
        override = _directive_value(
            validated_directives, DirectiveType.MINIMUM_BATTERY_RESERVE, h, "minimum_energy_kwh"
        )
        if override is not None:
            # Take the stricter (larger) of base floor and directive floor.
            effective_floor = max(min_soc, override)
            prob += soc[h] >= effective_floor, f"min_reserve_{h}"

    # Dynamic per-hour grid cap.
    for h in max_grid_hours:
        cap = _directive_value(
            validated_directives, DirectiveType.MAX_GRID_WINDOW, h, "max_grid_kwh"
        )
        if cap is not None:
            prob += grid[h] <= cap, f"max_grid_{h}"

    # 6) End-of-day neutrality
    prob += soc[23] == initial_soc, "end_of_day_neutrality"

    # --------------------------------------------------------- solve LP
    # Try solvers in order of preference. PuLP 3.x ships HiGHS (high-performance
    # open-source LP/MIP solver) bundled as a wheel — no system binary needed.
    # Fall back to CBC, then GLPK, then any other solver pulp can discover.
    last_error: Exception | None = None
    status = None
    solver_candidates = []
    try:
        solver_candidates.append(pulp.HiGHS_CMD(msg=False))
    except Exception:
        pass
    try:
        solver_candidates.append(pulp.PULP_CBC_CMD(msg=False))
    except Exception:
        pass
    try:
        solver_candidates.append(pulp.GLPK_CMD(msg=False))
    except Exception:
        pass

    solved = False
    for solver in solver_candidates:
        try:
            status = prob.solve(solver)
            if pulp.LpStatus[status] == "Optimal":
                solved = True
                break
        except Exception as exc:  # noqa: BLE001 - intentional fallback
            last_error = exc
            continue

    if not solved:
        if last_error is not None:
            raise RuntimeError(f"No LP solver available: {last_error}") from last_error
        raise RuntimeError(
            f"LP did not reach optimality. Solver status: {pulp.LpStatus[status]}"
        )

    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"LP did not reach optimality. Solver status: {pulp.LpStatus[status]}"
        )

    # ----------------------------------------------- extract & verify
    hourly_plan: List[HourlyPlanEntry] = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for h in hours:
        g = float(grid[h].value() or 0.0)
        su = float(solar_used[h].value() or 0.0)
        ch = float(charge[h].value() or 0.0)
        dis = float(discharge[h].value() or 0.0)
        soc_h = float(soc[h].value() or 0.0)

        # Snap numerical noise to 0 so we don't report 1e-10 kWh.
        if abs(g) < EPS:
            g = 0.0
        if abs(su) < EPS:
            su = 0.0
        if abs(ch) < EPS:
            ch = 0.0
        if abs(dis) < EPS:
            dis = 0.0
        if abs(soc_h) < EPS:
            soc_h = 0.0

        # Battery action & magnitude.
        if ch > ACTION_EPS and dis > ACTION_EPS:
            # Solver shouldn't produce this for a balanced LP; force idle.
            action = BatteryAction.IDLE
            magnitude = 0.0
        elif ch > ACTION_EPS:
            action = BatteryAction.CHARGE
            magnitude = ch
        elif dis > ACTION_EPS:
            action = BatteryAction.DISCHARGE
            magnitude = dis
        else:
            action = BatteryAction.IDLE
            magnitude = 0.0

        cost_h = g * tariff[h]
        total_grid += g
        total_cost += cost_h
        peak_grid = max(peak_grid, g)

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                demand_kwh=round(demand[h], ROUND_HOUR),
                solar_used_kwh=round(su, ROUND_HOUR),
                grid_kwh=round(g, ROUND_HOUR),
                charge_kwh=round(ch, ROUND_HOUR),
                discharge_kwh=round(dis, ROUND_HOUR),
                battery_action=action,
                battery_kwh=round(magnitude, ROUND_HOUR),
                battery_energy_after_kwh=round(soc_h, ROUND_HOUR),
                tariff_bdt_per_kwh=round(tariff[h], ROUND_HOUR),
            )
        )

    return OptimizationResult(
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid, ROUND_TOTAL),
        total_cost_bdt=round(total_cost, ROUND_TOTAL),
        peak_grid_kwh=round(peak_grid, ROUND_TOTAL),
    )


# --------------------------------------------------------------------------- #
# Independent recalculation (used by the API layer for response verification)
# --------------------------------------------------------------------------- #
def recompute_totals(hourly_plan: List[HourlyPlanEntry]) -> Tuple[float, float, float]:
    """Recompute total grid, total cost and peak grid from the final plan.

    This avoids floating-point mismatch issues between the LP objective value
    and the rounded per-hour figures that go into the JSON response.
    """

    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    for entry in hourly_plan:
        total_grid += entry.grid_kwh
        total_cost += entry.grid_kwh * entry.tariff_bdt_per_kwh
        peak_grid = max(peak_grid, entry.grid_kwh)
    return (
        round(total_grid, ROUND_TOTAL),
        round(total_cost, ROUND_TOTAL),
        round(peak_grid, ROUND_TOTAL),
    )