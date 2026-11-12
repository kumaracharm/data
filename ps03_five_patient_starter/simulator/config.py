from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

VITALS = ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")


@dataclass(frozen=True)
class VitalBaseline:
    hr_bpm: float
    spo2_percent: float
    rr_bpm: float
    temp_c: float
    provenance: str = "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC"

    def as_dict(self) -> dict[str, float]:
        return {
            "hr_bpm": self.hr_bpm,
            "spo2_percent": self.spo2_percent,
            "rr_bpm": self.rr_bpm,
            "temp_c": self.temp_c,
        }


@dataclass(frozen=True)
class PatientSimulationConfig:
    patient_id: str
    seed: int
    baseline: VitalBaseline
    sampling_interval_seconds: float = 1.0
    scenario: str = "stable_recovery"
    scenario_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SimulatorConfig:
    seed: int = 20261112
    duration_seconds: int = 120
    sampling_interval_seconds: float = 1.0
    speed: float = 1.0
    realtime: bool = False
    gateway_url: str | None = None
    missing_probability: float = 0.01
    start_timestamp: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.sampling_interval_seconds <= 0:
            raise ValueError("sampling_interval_seconds must be positive")
        if self.speed <= 0:
            raise ValueError("speed must be positive")
        if not 0 <= self.missing_probability <= 1:
            raise ValueError("missing_probability must be between 0 and 1")

    @property
    def tick_count(self) -> int:
        return max(1, math.ceil(self.duration_seconds / self.sampling_interval_seconds))


@dataclass(frozen=True)
class NoiseParameters:
    hr_bpm_sd: float = 1.0
    spo2_percent_sd: float = 0.25
    rr_bpm_sd: float = 0.5
    temp_c_sd: float = 0.04

    @classmethod
    def from_analysis(cls, analysis: dict[str, Any]) -> "NoiseParameters":
        variability = analysis.get("pooled", {}).get("short_term_difference_std", {})
        noise_config_path = DATA / "simulation" / "noise_config.json"
        noise_config = json.loads(noise_config_path.read_text(encoding="utf-8"))
        multipliers = noise_config.get("bidmc_difference_std_multipliers", {})

        def channel(name: str, fallback: float) -> float:
            measured = variability.get(name)
            if not isinstance(measured, (int, float)) or measured <= 0:
                return fallback
            multiplier = float(multipliers.get(name, 0.5))
            return max(0.01, float(measured) * multiplier)

        return cls(
            hr_bpm_sd=channel("hr_bpm", 1.0),
            spo2_percent_sd=channel("spo2_percent", 0.25),
            rr_bpm_sd=channel("rr_bpm", 0.5),
            temp_c_sd=float(noise_config.get("temp_c_sd", 0.04)),
        )


def load_provisional_baseline(path: Path | None = None) -> VitalBaseline:
    config_path = path or DATA / "simulation" / "initial_values.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("configuration_status") != "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC":
        raise ValueError("Initial values must retain their provisional, non-patient-specific label.")
    values = data["initial_values"]
    if set(values) != set(VITALS):
        raise ValueError(f"Initial values must include exactly {VITALS}")
    return VitalBaseline(**{key: float(values[key]) for key in VITALS})


def load_patient_baseline(
    patient_id: str, path: Path | None = None
) -> VitalBaseline:
    config_path = path or DATA / "simulation" / "initial_values.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("configuration_status") != "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC":
        raise ValueError("Initial values must retain their provisional, non-clinical label.")
    overrides = data.get("patient_overrides", {})
    unknown_ids = set(overrides) - {f"P{number:03d}" for number in range(1, 11)}
    if unknown_ids:
        raise ValueError(f"Initial-value overrides contain unknown patient IDs: {sorted(unknown_ids)}")
    values = dict(data["initial_values"])
    provenance = "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC"
    if patient_id in overrides:
        patient_values = overrides[patient_id]
        if set(patient_values) != set(VITALS):
            raise ValueError(
                f"{patient_id}: patient baseline override must include exactly {VITALS}"
            )
        values.update(patient_values)
        provenance = "PROVISIONAL_PATIENT_KEYED_DEMO_OVERRIDE_NOT_CLINICAL"
    if set(values) != set(VITALS):
        raise ValueError(f"Initial values must include exactly {VITALS}")
    return VitalBaseline(
        **{key: float(values[key]) for key in VITALS},
        provenance=provenance,
    )


def patient_config(
    patient_id: str,
    *,
    seed: int,
    baseline: VitalBaseline,
    sampling_interval_seconds: float = 1.0,
    scenario: str = "stable_recovery",
    scenario_parameters: dict[str, Any] | None = None,
) -> PatientSimulationConfig:
    return PatientSimulationConfig(
        patient_id=patient_id,
        seed=seed,
        baseline=baseline,
        sampling_interval_seconds=sampling_interval_seconds,
        scenario=scenario,
        scenario_parameters=scenario_parameters or {},
    )
