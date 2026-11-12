from __future__ import annotations

import unittest

import pandas as pd

from simulator.bidmc_loader import iter_bidmc_replay, load_bidmc_recording
from simulator.bidmc_analysis import analyze_bidmc


class BidmcLoaderTests(unittest.TestCase):
    def test_replay_preserves_source_readings_without_patient_identity(self) -> None:
        original = pd.read_csv("data/bidmc_original/bidmc_01_Numerics.csv")
        mapped = load_bidmc_recording("bidmc01")
        replay = list(iter_bidmc_replay("bidmc01"))
        self.assertEqual(len(replay), len(original))
        self.assertTrue(
            (pd.to_numeric(original[" HR"], errors="coerce").to_numpy()
             == mapped["hr_bpm"].to_numpy()).all()
        )
        self.assertNotIn("patient_id", replay[0])
        self.assertIsNone(replay[0]["temp_c"])
        self.assertEqual(replay[0]["source_time_s"], 0)
        self.assertIn("temperature_status", replay[0])

    def test_all_recordings_have_statistics_and_missingness(self) -> None:
        analysis = analyze_bidmc()
        self.assertEqual(analysis["recording_count"], 5)
        self.assertEqual(
            {recording["recording_id"] for recording in analysis["recordings"]},
            {f"bidmc{i:02}" for i in range(1, 6)},
        )
        self.assertIn("short_term_difference_std", analysis["pooled"])
        self.assertIn("pairwise_signal_correlations", analysis["pooled"])
        self.assertGreater(
            analysis["recordings"][0]["channels"]["spo2_percent"]["missing_percent"], 0
        )
        self.assertAlmostEqual(
            analysis["recordings"][0]["sampling_interval_seconds"]["median"], 1.0
        )


if __name__ == "__main__":
    unittest.main()
