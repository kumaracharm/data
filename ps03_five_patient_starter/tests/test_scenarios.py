from __future__ import annotations

import unittest

from simulator.scenario_engine import SCENARIOS, ScenarioEngine


class ScenarioTests(unittest.TestCase):
    def test_every_mandatory_scenario_executes(self) -> None:
        mandatory = {
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
        }
        self.assertTrue(mandatory.issubset(SCENARIOS))
        for scenario_name in mandatory:
            engine = ScenarioEngine("P010", scenario_name, 10, 100)
            step = engine.step(engine.onset_tick)
            self.assertEqual(step.name, scenario_name)
            self.assertEqual(
                step.event_id is not None,
                scenario_name != "stable_recovery",
            )

    def test_genuine_and_sensor_fault_scenarios_are_separate(self) -> None:
        deterioration = ScenarioEngine("P001", "gradual_deterioration", 1, 100)
        step = deterioration.step(deterioration.end_tick)
        self.assertGreater(step.physiological_changes["hr_bpm"], 0)
        self.assertLess(step.physiological_changes["spo2_percent"], 0)
        self.assertIsNone(step.sensor_fault_label)
        combined = ScenarioEngine("P001", "deterioration_with_sensor_failure", 1, 100)
        combined_step = combined.step(combined.end_tick)
        self.assertGreater(combined_step.physiological_changes["hr_bpm"], 0)
        self.assertEqual(
            combined_step.sensor_fault_label,
            "PROBE_REMOVAL_DURING_PHYSIOLOGICAL_DETERIORATION",
        )

    def test_recovery_returns_changes_to_baseline(self) -> None:
        engine = ScenarioEngine("P001", "recovery_after_deterioration", 1, 100)
        peak = engine.step(engine.onset_tick + (engine.end_tick - engine.onset_tick) // 2)
        end = engine.step(engine.end_tick)
        self.assertGreater(peak.physiological_changes["hr_bpm"], 0)
        self.assertAlmostEqual(end.physiological_changes["hr_bpm"], 0.0)

    def test_all_required_scenarios_inject_the_expected_effects(self) -> None:
        fault_scenarios = {
            "motion_artifacts": "MOTION_ARTIFACT",
            "probe_removal": "PROBE_REMOVAL",
            "flatlined_sensor": "FLATLINED_SENSOR",
            "missing_measurements": "INTERMITTENT_MISSING_MEASUREMENT",
            "connectivity_loss": "CONNECTIVITY_LOSS",
        }
        for scenario_name, expected_fault in fault_scenarios.items():
            with self.subTest(scenario=scenario_name):
                engine = ScenarioEngine("P001", scenario_name, 11, 100)
                step = engine.step(engine.onset_tick)
                self.assertTrue(step.active)
                self.assertEqual(step.sensor_fault_label, expected_fault)
                self.assertEqual(step.physiological_changes, {})

        gradual = ScenarioEngine("P001", "gradual_deterioration", 11, 100)
        gradual_step = gradual.step(gradual.end_tick)
        self.assertIsNone(gradual_step.sensor_fault_label)
        self.assertEqual(set(gradual_step.physiological_changes), {
            "hr_bpm", "spo2_percent", "rr_bpm", "temp_c",
        })

        sudden = ScenarioEngine("P001", "sudden_deterioration", 11, 100)
        sudden_step = sudden.step(sudden.onset_tick)
        self.assertEqual(sudden_step.physiological_changes, sudden.deterioration)
        self.assertIsNone(sudden_step.sensor_fault_label)

        fluctuations = ScenarioEngine("P001", "temporary_fluctuations", 11, 100)
        fluctuation_tick = fluctuations.onset_tick + (
            fluctuations.end_tick - fluctuations.onset_tick
        ) // 8
        fluctuation_step = fluctuations.step(fluctuation_tick)
        self.assertTrue(fluctuation_step.physiological_changes)
        self.assertIsNone(fluctuation_step.sensor_fault_label)

        combined = ScenarioEngine(
            "P001", "deterioration_with_sensor_failure", 11, 100
        )
        combined_step = combined.step(combined.end_tick)
        self.assertTrue(combined_step.physiological_changes)
        self.assertEqual(
            combined_step.sensor_fault_label,
            "PROBE_REMOVAL_DURING_PHYSIOLOGICAL_DETERIORATION",
        )

        recovery = ScenarioEngine("P001", "recovery_after_deterioration", 11, 100)
        recovery_peak = recovery.step(
            recovery.onset_tick + (recovery.end_tick - recovery.onset_tick) // 2
        )
        self.assertGreater(recovery_peak.physiological_changes["hr_bpm"], 0)
        self.assertIsNone(recovery_peak.sensor_fault_label)

    def test_scenario_parameters_control_bounded_physiology(self) -> None:
        engine = ScenarioEngine(
            "P001",
            "sudden_deterioration",
            11,
            100,
            {"physiological_changes": {"hr_bpm": 35.0}},
        )
        self.assertEqual(
            engine.step(engine.onset_tick).physiological_changes["hr_bpm"],
            35.0,
        )


if __name__ == "__main__":
    unittest.main()
