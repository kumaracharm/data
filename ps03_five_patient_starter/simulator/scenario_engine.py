from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCENARIOS = {
    "stable_recovery",
    "gradual_deterioration",
    "sudden_deterioration",
    "temporary_fluctuations",
    "motion_artifacts",
    "probe_removal",
    "flatlined_sensor",
    "missing_measurements",
    "connectivity_loss",
    "deterioration_with_sensor_failure",
    "recovery_after_deterioration",
    "temporary_signal_degradation",
}

DETERIORATION = {
    "hr_bpm": 18.0,
    "spo2_percent": -5.0,
    "rr_bpm": 8.0,
    "temp_c": 0.8,
}


@dataclass(frozen=True)
class ScenarioStep:
    name: str
    event_id: str | None
    active: bool
    onset_tick: int | None
    end_tick: int | None
    physiological_changes: dict[str, float]
    sensor_fault_label: str | None


class ScenarioEngine:
    def __init__(
        self,
        patient_id: str,
        name: str,
        seed: int,
        total_ticks: int,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"Unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")
        self.patient_id = patient_id
        self.name = name
        self.seed = seed
        self.total_ticks = total_ticks
        self.parameters = parameters or {}
        self.onset_tick = int(
            self.parameters.get(
                "onset_tick",
                total_ticks * float(self.parameters.get("onset_fraction", 0.25)),
            )
        )
        default_end = int(
            total_ticks * float(self.parameters.get("end_fraction", 0.75))
        )
        if name in {"connectivity_loss", "flatlined_sensor"}:
            default_end = total_ticks - 1
        self.end_tick = int(self.parameters.get("end_tick", default_end))
        self.onset_tick = min(max(self.onset_tick, 0), total_ticks - 1)
        self.end_tick = min(max(self.end_tick, self.onset_tick), total_ticks - 1)
        self.event_id = f"{patient_id}-{name}-{seed}"
        self.deterioration = dict(DETERIORATION)
        self.deterioration.update(self.parameters.get("physiological_changes", {}))

    def step(self, tick: int) -> ScenarioStep:
        active = self.onset_tick <= tick <= self.end_tick and self.name != "stable_recovery"
        changes: dict[str, float] = {}
        sensor_fault: str | None = None
        fraction = (
            (tick - self.onset_tick) / max(1, self.end_tick - self.onset_tick)
            if active
            else 0.0
        )

        if active and self.name == "gradual_deterioration":
            changes = {key: value * fraction for key, value in self.deterioration.items()}
        elif active and self.name == "sudden_deterioration":
            changes = dict(self.deterioration)
        elif active and self.name == "temporary_fluctuations":
            import math

            phase = (tick - self.onset_tick) / max(1, self.end_tick - self.onset_tick)
            amplitude = math.sin(phase * 4 * math.pi)
            changes = {
                "hr_bpm": 8.0 * amplitude,
                "spo2_percent": -2.0 * abs(amplitude),
                "rr_bpm": 3.0 * amplitude,
            }
        elif active and self.name == "recovery_after_deterioration":
            deterioration_peak = 0.45
            recovery_fraction = max(0.0, (fraction - deterioration_peak) / (1.0 - deterioration_peak))
            scale = min(1.0, fraction / deterioration_peak) * (1.0 - recovery_fraction)
            changes = {key: value * scale for key, value in self.deterioration.items()}

        if active:
            if self.name == "motion_artifacts":
                sensor_fault = "MOTION_ARTIFACT"
            elif self.name == "probe_removal":
                sensor_fault = "PROBE_REMOVAL"
            elif self.name == "flatlined_sensor":
                sensor_fault = "FLATLINED_SENSOR"
            elif self.name == "missing_measurements":
                sensor_fault = "INTERMITTENT_MISSING_MEASUREMENT"
            elif self.name == "connectivity_loss":
                sensor_fault = "CONNECTIVITY_LOSS"
            elif self.name == "temporary_signal_degradation":
                sensor_fault = "TEMPORARY_SIGNAL_DEGRADATION"
            elif self.name == "deterioration_with_sensor_failure":
                changes = {
                    key: value * fraction for key, value in self.deterioration.items()
                }
                if fraction >= float(self.parameters.get("sensor_failure_fraction", 0.6)):
                    sensor_fault = "PROBE_REMOVAL_DURING_PHYSIOLOGICAL_DETERIORATION"
        return ScenarioStep(
            name=self.name,
            event_id=self.event_id if active else None,
            active=active,
            onset_tick=self.onset_tick if self.name != "stable_recovery" else None,
            end_tick=self.end_tick if self.name != "stable_recovery" else None,
            physiological_changes=changes,
            sensor_fault_label=sensor_fault,
        )
