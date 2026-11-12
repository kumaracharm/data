from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .config import NoiseParameters
from .scenario_engine import ScenarioStep


OBSERVED_CHANNELS = ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")


class SensorModel:
    """Adds measurement noise and sensor failures without mutating true physiology."""

    def __init__(
        self,
        seed: int,
        noise: NoiseParameters | None = None,
        missing_probability: float = 0.08,
        parameters: Mapping[str, float] | None = None,
    ) -> None:
        if not 0 <= missing_probability <= 1:
            raise ValueError("missing_probability must be between 0 and 1")
        self.rng = np.random.default_rng(seed)
        self.noise = noise or NoiseParameters()
        self.missing_probability = missing_probability
        self.parameters = dict(parameters or {})
        self._previous_valid: dict[str, float] = {}
        self._flatline_values: dict[str, float] | None = None

    def observe(
        self,
        truth: dict[str, Any],
        step: ScenarioStep,
        *,
        timestamp: str,
        event_id: str,
        patient_id: str,
    ) -> dict[str, Any]:
        values = {
            "hr_bpm": float(truth["true_hr_bpm"]),
            "spo2_percent": float(truth["true_spo2_percent"]),
            "rr_bpm": float(truth["true_rr_bpm"]),
            "temp_c": float(truth["true_temp_c"]),
        }
        sds = {
            "hr_bpm": self.noise.hr_bpm_sd,
            "spo2_percent": self.noise.spo2_percent_sd,
            "rr_bpm": self.noise.rr_bpm_sd,
            "temp_c": self.noise.temp_c_sd,
        }
        for channel in OBSERVED_CHANNELS:
            values[channel] += float(self.rng.normal(0.0, sds[channel]))

        fault = step.sensor_fault_label
        connected = fault != "CONNECTIVITY_LOSS"
        quality = float(np.clip(1.0 - sum(abs(v) for v in sds.values()) / 30.0, 0.2, 1.0))
        status = "CONNECTED"
        if fault == "MOTION_ARTIFACT":
            values["hr_bpm"] += float(
                self.rng.choice([-1, 1])
                * self.rng.uniform(
                    self.parameters.get("motion_hr_min", 18.0),
                    self.parameters.get("motion_hr_max", 35.0),
                )
            )
            values["spo2_percent"] += float(
                self.rng.uniform(
                    self.parameters.get("motion_spo2_min", -8.0),
                    self.parameters.get("motion_spo2_max", -3.0),
                )
            )
            values["rr_bpm"] += float(
                self.rng.uniform(
                    self.parameters.get("motion_rr_min", 4.0),
                    self.parameters.get("motion_rr_max", 10.0),
                )
            )
            quality = 0.25
            status = "DEGRADED_MOTION_ARTIFACT"
        elif fault == "PROBE_REMOVAL":
            values["hr_bpm"] = float("nan")
            values["spo2_percent"] = float("nan")
            quality = 0.0
            status = "PROBE_REMOVED"
        elif fault == "PROBE_REMOVAL_DURING_PHYSIOLOGICAL_DETERIORATION":
            values["hr_bpm"] = float("nan")
            values["spo2_percent"] = float("nan")
            quality = 0.0
            status = "PROBE_REMOVED_DURING_DETERIORATION"
        elif fault == "FLATLINED_SENSOR":
            if self._flatline_values is None:
                self._flatline_values = {
                    key: self._previous_valid.get(key, values[key])
                    for key in OBSERVED_CHANNELS
                }
            values.update(self._flatline_values)
            quality = 0.0
            status = "FLATLINED"
        elif fault == "INTERMITTENT_MISSING_MEASUREMENT":
            for channel in OBSERVED_CHANNELS:
                if self.rng.random() < self.missing_probability:
                    values[channel] = float("nan")
            quality = 0.5
            status = "INTERMITTENT_MISSING"
        elif fault == "CONNECTIVITY_LOSS":
            values = {channel: float("nan") for channel in OBSERVED_CHANNELS}
            quality = 0.0
            status = "DISCONNECTED"
        elif fault == "TEMPORARY_SIGNAL_DEGRADATION":
            for channel in OBSERVED_CHANNELS:
                values[channel] += float(self.rng.normal(0, sds[channel] * 4))
            quality = 0.4
            status = "TEMPORARY_SIGNAL_DEGRADATION"

        if connected and fault != "FLATLINED_SENSOR":
            for channel in OBSERVED_CHANNELS:
                if np.isfinite(values[channel]):
                    self._previous_valid[channel] = values[channel]

        return {
            "event_id": event_id,
            "patient_id": patient_id,
            "timestamp": timestamp,
            "observed_hr_bpm": _nullable(values["hr_bpm"]),
            "observed_spo2_percent": _nullable(values["spo2_percent"]),
            "observed_rr_bpm": _nullable(values["rr_bpm"]),
            "observed_temp_c": _nullable(values["temp_c"]),
            "signal_quality": round(quality, 4),
            "sensor_status": status,
            "sensor_connected": connected,
        }


def _nullable(value: float) -> float | None:
    return round(float(value), 4) if np.isfinite(value) else None
