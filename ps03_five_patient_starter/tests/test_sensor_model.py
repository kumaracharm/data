from __future__ import annotations

import unittest

from simulator.config import NoiseParameters
from simulator.scenario_engine import ScenarioEngine
from simulator.sensor_model import SensorModel


TRUTH = {
    "patient_id": "P001",
    "true_hr_bpm": 72.0,
    "true_spo2_percent": 97.0,
    "true_rr_bpm": 16.0,
    "true_temp_c": 36.8,
}


class SensorModelTests(unittest.TestCase):
    def _step(self, scenario: str):
        engine = ScenarioEngine("P001", scenario, 1, 4, {"onset_tick": 0, "end_tick": 3})
        return engine.step(1)

    def test_noise_changes_observed_values_only(self) -> None:
        truth = dict(TRUTH)
        model = SensorModel(2, NoiseParameters(2, 1, 2, 0.1))
        output = model.observe(
            truth, self._step("stable_recovery"), timestamp="2026-01-01T00:00:00Z",
            event_id="e1", patient_id="P001",
        )
        self.assertNotEqual(output["observed_hr_bpm"], truth["true_hr_bpm"])
        self.assertEqual(truth, TRUTH)
        self.assertFalse(any(key.startswith("true_") for key in output))
        self.assertNotIn("scenario_name", output)

    def test_missing_and_probe_removal_values_are_null_not_zero(self) -> None:
        model = SensorModel(3, NoiseParameters(0, 0, 0, 0))
        output = model.observe(
            TRUTH, self._step("probe_removal"), timestamp="2026-01-01T00:00:00Z",
            event_id="e2", patient_id="P001",
        )
        self.assertIsNone(output["observed_hr_bpm"])
        self.assertIsNone(output["observed_spo2_percent"])
        self.assertIsNot(output["observed_hr_bpm"], 0)

    def test_connectivity_loss_is_explicit(self) -> None:
        model = SensorModel(4, NoiseParameters(0, 0, 0, 0))
        output = model.observe(
            TRUTH, self._step("connectivity_loss"), timestamp="2026-01-01T00:00:00Z",
            event_id="e3", patient_id="P001",
        )
        self.assertFalse(output["sensor_connected"])
        self.assertEqual(output["sensor_status"], "DISCONNECTED")
        self.assertTrue(all(output[f"observed_{name}"] is None for name in ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")))

    def test_motion_flatline_and_intermittent_dropout_are_sensor_only(self) -> None:
        motion_model = SensorModel(5, NoiseParameters(0, 0, 0, 0))
        motion = motion_model.observe(
            TRUTH, self._step("motion_artifacts"), timestamp="2026-01-01T00:00:00Z",
            event_id="motion", patient_id="P001",
        )
        self.assertEqual(motion["sensor_status"], "DEGRADED_MOTION_ARTIFACT")
        self.assertEqual(motion["signal_quality"], 0.25)
        self.assertEqual(TRUTH["true_hr_bpm"], 72.0)

        flatline_model = SensorModel(6, NoiseParameters(0, 0, 0, 0))
        flatline_step = self._step("flatlined_sensor")
        first = flatline_model.observe(
            TRUTH, flatline_step, timestamp="2026-01-01T00:00:00Z",
            event_id="flat-1", patient_id="P001",
        )
        changed_truth = {**TRUTH, "true_hr_bpm": 100.0, "true_spo2_percent": 90.0}
        second = flatline_model.observe(
            changed_truth, flatline_step, timestamp="2026-01-01T00:00:01Z",
            event_id="flat-2", patient_id="P001",
        )
        self.assertEqual(first["sensor_status"], "FLATLINED")
        self.assertEqual(
            [first[f"observed_{channel}"] for channel in ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")],
            [second[f"observed_{channel}"] for channel in ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")],
        )

        dropout_model = SensorModel(
            7, NoiseParameters(0, 0, 0, 0), missing_probability=1
        )
        dropout = dropout_model.observe(
            TRUTH, self._step("missing_measurements"),
            timestamp="2026-01-01T00:00:00Z", event_id="missing", patient_id="P001",
        )
        self.assertEqual(dropout["sensor_status"], "INTERMITTENT_MISSING")
        self.assertTrue(
            all(
                dropout[f"observed_{channel}"] is None
                for channel in ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")
            )
        )
        self.assertFalse(any(value == 0 for key, value in dropout.items() if key.startswith("observed_")))


if __name__ == "__main__":
    unittest.main()
