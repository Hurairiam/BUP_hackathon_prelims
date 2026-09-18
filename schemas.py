"""
Pydantic v2 data models for the GridWise Smart Campus Energy Optimization API.

This module defines:
    - Strict Enums for directive types and battery actions.
    - Request and response schemas for the /optimize-energy endpoint.
    - Field-level and model-level validators implementing the auto-healing
      guardrails specified in Phase 2 of the hackathon brief:
        * Hour Array Normalizer   -> deduplicate + sort 0..23 ints.
        * Applies Enforcer        -> no_op forces applies=False & adjustment=None.
        * Solar Factor Clamper    -> clamp factor between 0.0 and 1.0.

All numeric fields that flow into the LP solver are explicitly typed to keep
the contract between LLM output, validators and PuLP clean.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


# --------------------------------------------------------------------------- #
# Input / scenario models (24-hour scenario)
# --------------------------------------------------------------------------- #
class OperatorNote(BaseModel):
    """A free-form operator note as supplied in the request."""

    model_config = ConfigDict(extra="allow")

    text: str = Field(..., description="Raw operator note text.")


class ScenarioRequest(BaseModel):
    """Full 24-hour scenario that drives the optimization."""

    model_config = ConfigDict(extra="allow")

    scenario_id: str = Field(..., description="Unique identifier for this scenario.")
    battery_capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    min_energy_kwh: float = Field(0.0, ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    # 24-element arrays (one value per hour, 0..23)
    demand_kwh: List[float] = Field(..., min_length=24, max_length=24)
    solar_kwh: List[float] = Field(..., min_length=24, max_length=24)
    tariff_bdt_per_kwh: List[float] = Field(..., min_length=24, max_length=24)

    operator_notes: List[str] = Field(
        default_factory=list,
        description="1-3 free-form operator notes.",
    )

    @field_validator("operator_notes")
    @classmethod
    def _notes_bounds(cls, v: List[str]) -> List[str]:
        if len(v) > 3:
            raise ValueError("At most 3 operator_notes are supported.")
        return v

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def _non_negative(cls, v: List[float]) -> List[float]:
        for x in v:
            if x < 0:
                raise ValueError("Hourly arrays must contain non-negative values.")
        return v


# --------------------------------------------------------------------------- #
# LLM-extracted directive models (with auto-healing validators)
# --------------------------------------------------------------------------- #
class StructuredAdjustment(BaseModel):
    """Structured payload attached to a directive.

    Fields are optional because different directive_types populate different
    subsets. Extra fields are ignored to keep LLM noise from breaking parsing.
    """

    model_config = ConfigDict(extra="ignore")

    hours: Optional[List[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None


class ExtractedDirective(BaseModel):
    """A single LLM-extracted directive, after auto-healing.

    Auto-healing rules (applied via validators below):
        1. Hours list is deduplicated, sorted and constrained to 0..23.
        2. If directive_type == "no_op" then applies=False and
           structured_adjustment=None.
        3. For directive_type == "solar_reduction", factor is clamped to [0, 1].
        4. For every non-no_op directive, applies is forced True.
    """

    model_config = ConfigDict(extra="ignore")

    note_index: int = Field(..., ge=0)
    note_text: str = ""
    directive_type: DirectiveType
    applies: bool = True
    structured_adjustment: Optional[StructuredAdjustment] = None
    reasoning: Optional[str] = None

    # ---- field-level validators ------------------------------------------ #
    @field_validator("structured_adjustment")
    @classmethod
    def _heal_adjustment(
        cls, v: Optional[StructuredAdjustment]
    ) -> Optional[StructuredAdjustment]:
        """Hour array normalizer + solar factor clamper."""

        if v is None:
            return None

        # 1) Hour array: deduplicate, sort, clamp to 0..23.
        if v.hours is not None:
            cleaned: List[int] = []
            for h in v.hours:
                try:
                    hi = int(h)
                except (TypeError, ValueError):
                    continue
                if 0 <= hi <= 23:
                    cleaned.append(hi)
            v.hours = sorted(set(cleaned))

        # 3) Solar factor clamper.
        if v.factor is not None:
            try:
                f = float(v.factor)
            except (TypeError, ValueError):
                f = 0.0
            # Clamp into [0.0, 1.0].
            v.factor = max(0.0, min(1.0, f))

        return v

    # ---- model-level validator: applies enforcer -------------------------- #
    @model_validator(mode="after")
    def _enforce_applies_rule(self) -> "ExtractedDirective":
        # 2) "no_op" -> applies False & no structured adjustment.
        if self.directive_type == DirectiveType.NO_OP:
            self.applies = False
            self.structured_adjustment = None
        else:
            # Everything else -> applies True.
            self.applies = True
        return self


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class DirectiveInterpretation(BaseModel):
    """Human/LLM-facing interpretation summary returned to the caller."""

    model_config = ConfigDict(extra="ignore")

    note_index: int
    note_text: str
    directive_type: DirectiveType
    applies: bool
    reasoning: Optional[str] = None
    structured_adjustment: Optional[StructuredAdjustment] = None


class HourlyPlanEntry(BaseModel):
    """Per-hour operating plan returned in the response."""

    model_config = ConfigDict(extra="ignore")

    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float
    solar_used_kwh: float
    grid_kwh: float
    charge_kwh: float
    discharge_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float
    tariff_bdt_per_kwh: float


class OptimizeResponse(BaseModel):
    """Strict response schema for POST /optimize-energy."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
