from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import NoiseParameters, PatientSimulationConfig


@dataclass(frozen=True)
class TrueVitals:
    hr_bpm: float
    spo2_percent: float
    rr_bpm: float
    temp_c: float

    def as_dict(self) -> dict[str, float]:
        return {
            "hr_bpm": self.hr_bpm,
            "spo2_percent": self.spo2_percent,
            "rr_bpm": self.rr_bpm,
            "temp_c": self.temp_c,
        }


class PhysiologicalEngine:
    """Generates simulated physiology from a configurable baseline and scenario deltas."""

    def __init__(
        self, config: PatientSimulationConfig, noise: NoiseParameters | None = None
    ) -> None:
        self.config = config
        self.noise = noise or NoiseParameters()
        self.rng = np.random.default_rng(config.seed)
        self._previous_noise = np.zeros(4, dtype=float)
        self._noise_sd = np.array(
            [
                self.noise.hr_bpm_sd,
                self.noise.spo2_percent_sd,
                self.noise.rr_bpm_sd,
                self.noise.temp_c_sd,
            ],
            dtype=float,
        )

    def step(
        self, tick: int, changes: dict[str, float] | None = None
    ) -> dict[str, object]:
        changes = changes or {}
        innovations = self.rng.normal(0.0, self._noise_sd * 0.5, size=4)
        self._previous_noise = 0.65 * self._previous_noise + innovations
        baselines = self.config.baseline.as_dict()
        values = {
            "hr_bpm": baselines["hr_bpm"] + float(self._previous_noise[0]),
            "spo2_percent": baselines["spo2_percent"] + float(self._previous_noise[1]),
            "rr_bpm": baselines["rr_bpm"] + float(self._previous_noise[2]),
            "temp_c": baselines["temp_c"] + float(self._previous_noise[3]),
        }
        for channel, change in changes.items():
            if channel not in values:
                raise ValueError(f"Unknown physiological channel {channel!r}")
            values[channel] += float(change)
        values["hr_bpm"] = float(np.clip(values["hr_bpm"], 25, 240))
        values["spo2_percent"] = float(np.clip(values["spo2_percent"], 50, 100))
        values["rr_bpm"] = float(np.clip(values["rr_bpm"], 3, 70))
        values["temp_c"] = float(np.clip(values["temp_c"], 30, 43))
        return {
            "patient_id": self.config.patient_id,
            "tick": tick,
            "true_hr_bpm": values["hr_bpm"],
            "true_spo2_percent": values["spo2_percent"],
            "true_rr_bpm": values["rr_bpm"],
            "true_temp_c": values["temp_c"],
        }
