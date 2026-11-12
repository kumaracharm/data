from __future__ import annotations

import unittest

from simulator.config import NoiseParameters, VitalBaseline, patient_config
from simulator.physiological_engine import PhysiologicalEngine


class PhysiologicalEngineTests(unittest.TestCase):
    def test_reproducibility_and_independent_patient_rng(self) -> None:
        baseline = VitalBaseline(72.0, 97.0, 16.0, 36.8)
        first = PhysiologicalEngine(patient_config("P001", seed=7, baseline=baseline))
        repeat = PhysiologicalEngine(patient_config("P001", seed=7, baseline=baseline))
        other = PhysiologicalEngine(patient_config("P002", seed=8, baseline=baseline))
        first_rows = [first.step(tick) for tick in range(8)]
        repeat_rows = [repeat.step(tick) for tick in range(8)]
        other_rows = [other.step(tick) for tick in range(8)]
        self.assertEqual(first_rows, repeat_rows)
        self.assertNotEqual(first_rows, other_rows)
        self.assertEqual(first_rows[0]["patient_id"], "P001")

    def test_configured_initial_values_are_not_replaced_by_source_values(self) -> None:
        baseline = VitalBaseline(72.0, 97.0, 16.0, 36.8)
        engine = PhysiologicalEngine(
            patient_config("P001", seed=9, baseline=baseline),
            NoiseParameters(0, 0, 0, 0),
        )
        value = engine.step(0)
        self.assertEqual(value["true_hr_bpm"], baseline.hr_bpm)
        self.assertEqual(value["true_spo2_percent"], baseline.spo2_percent)
        self.assertEqual(value["true_rr_bpm"], baseline.rr_bpm)
        self.assertEqual(value["true_temp_c"], baseline.temp_c)

    def test_scenario_changes_apply_only_to_true_physiology(self) -> None:
        baseline = VitalBaseline(72.0, 97.0, 16.0, 36.8)
        engine = PhysiologicalEngine(
            patient_config("P001", seed=5, baseline=baseline),
            NoiseParameters(0, 0, 0, 0),
        )
        output = engine.step(5, {"hr_bpm": 10.0})
        self.assertEqual(output["true_hr_bpm"], 82.0)
        self.assertNotIn("observed_hr_bpm", output)

    def test_all_vital_trajectories_are_independent_and_clipped_to_bounds(self) -> None:
        engine = PhysiologicalEngine(
            patient_config(
                "P001",
                seed=17,
                baseline=VitalBaseline(72.0, 97.0, 16.0, 36.8),
            ),
            NoiseParameters(1, 0.5, 0.75, 0.1),
        )
        rows = [engine.step(tick) for tick in range(20)]
        for channel in (
            "true_hr_bpm",
            "true_spo2_percent",
            "true_rr_bpm",
            "true_temp_c",
        ):
            self.assertGreater(
                len({row[channel] for row in rows}),
                1,
                f"{channel} did not vary across samples",
            )
        extreme = engine.step(
            21,
            {
                "hr_bpm": 1000,
                "spo2_percent": -1000,
                "rr_bpm": 1000,
                "temp_c": -1000,
            },
        )
        self.assertEqual(extreme["true_hr_bpm"], 240)
        self.assertEqual(extreme["true_spo2_percent"], 50)
        self.assertEqual(extreme["true_rr_bpm"], 70)
        self.assertEqual(extreme["true_temp_c"], 30)


if __name__ == "__main__":
    unittest.main()
