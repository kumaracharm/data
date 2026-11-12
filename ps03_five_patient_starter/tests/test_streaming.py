from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path

from simulator.config import NoiseParameters, SimulatorConfig, VitalBaseline
from simulator.patient_loader import load_patients
from simulator.simulation_manager import EVENT_NAMESPACE, SimulationManager
from simulator.streaming_adapter import FORBIDDEN_OBSERVED_KEYS, StreamingAdapter


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ten_concurrent_streams_are_unique_and_ground_truth_is_separate(self) -> None:
        profiles = load_patients()
        config = SimulatorConfig(seed=12, duration_seconds=3)
        manager = SimulationManager(
            config,
            profiles=profiles,
            baseline=VitalBaseline(72, 97, 16, 36.8),
            noise=NoiseParameters(0, 0, 0, 0),
            scenario_map={profile.patient_id: "gradual_deterioration" for profile in profiles},
        )
        self.assertIsNot(manager.patient_states["P001"], manager.patient_states["P002"])
        manager.patient_states["P001"].sensor_status = "TEST_ONLY"
        self.assertEqual(manager.patient_states["P002"].sensor_status, "INITIALIZED")
        collected: list[dict] = []

        async def sink(record: dict) -> None:
            collected.append(record)

        adapter = StreamingAdapter(sink=sink)
        with tempfile.TemporaryDirectory() as folder:
            manifest = await manager.run_to_directory(Path(folder), adapter=adapter)
            rows = collected
            timestamps = [row["timestamp"] for row in rows]
            self.assertEqual(timestamps, sorted(timestamps))
            for timestamp in sorted(set(timestamps)):
                tick_rows = [row for row in rows if row["timestamp"] == timestamp]
                self.assertEqual(
                    [row["patient_id"] for row in tick_rows],
                    [profile.patient_id for profile in profiles],
                )
            self.assertEqual(manifest["concurrent_patient_streams"], 10)
            self.assertEqual(len(collected), 30)
            self.assertEqual(len({record["event_id"] for record in collected}), 30)
            self.assertFalse(
                any(FORBIDDEN_OBSERVED_KEYS.intersection(record) for record in collected)
            )
            for patient_id in (f"P{i:03}" for i in range(1, 11)):
                self.assertEqual(len(manager._ground_truth_rows[patient_id]), 3)
                self.assertTrue((Path(folder) / "observed" / f"{patient_id}.csv").is_file())
                self.assertTrue(
                    (Path(folder) / "ground_truth" / f"{patient_id}_ground_truth.csv").is_file()
                )

    async def test_random_seed_reproduces_observed_readings(self) -> None:
        profiles = load_patients()
        kwargs = dict(
            config=SimulatorConfig(seed=99, duration_seconds=4),
            profiles=profiles,
            baseline=VitalBaseline(72, 97, 16, 36.8),
            noise=NoiseParameters(0.1, 0.1, 0.1, 0.01),
            scenario_map={profile.patient_id: "stable_recovery" for profile in profiles},
        )
        first, second = SimulationManager(**kwargs), SimulationManager(**kwargs)
        rows_a = [row async for row in first.iter_observed()]
        rows_b = [row async for row in second.iter_observed()]
        key = lambda row: (row["patient_id"], row["timestamp"], row["event_id"])
        self.assertEqual(
            sorted(rows_a, key=key),
            sorted(rows_b, key=key),
        )
        first_event = next(row for row in rows_a if row["patient_id"] == "P001")
        self.assertEqual(
            first_event["event_id"],
            str(uuid.uuid5(EVENT_NAMESPACE, "P001:99:stable_recovery:0")),
        )

        different_seed = SimulationManager(
            **{
                **kwargs,
                "config": SimulatorConfig(seed=100, duration_seconds=4),
            }
        )
        rows_c = [row async for row in different_seed.iter_observed()]
        self.assertNotEqual(
            [
                row["observed_hr_bpm"]
                for row in rows_a
                if row["patient_id"] == "P001"
            ],
            [
                row["observed_hr_bpm"]
                for row in rows_c
                if row["patient_id"] == "P001"
            ],
        )
        self.assertNotEqual(
            first._ground_truth_rows["P001"],
            different_seed._ground_truth_rows["P001"],
        )

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            await first.run_to_directory(Path(first_dir))
            await second.run_to_directory(Path(second_dir))
            files_a = {
                path.relative_to(first_dir): path.read_bytes()
                for path in Path(first_dir).rglob("*")
                if path.is_file()
            }
            files_b = {
                path.relative_to(second_dir): path.read_bytes()
                for path in Path(second_dir).rglob("*")
                if path.is_file()
            }
            self.assertEqual(files_a, files_b)

    async def test_sensor_failure_does_not_stop_other_patient_streams(self) -> None:
        profiles = load_patients()[:2]
        manager = SimulationManager(
            SimulatorConfig(seed=21, duration_seconds=5, missing_probability=0),
            profiles=profiles,
            baseline=VitalBaseline(72, 97, 16, 36.8),
            noise=NoiseParameters(0, 0, 0, 0),
            scenario_map={
                "P001": "connectivity_loss",
                "P002": "stable_recovery",
            },
        )
        rows = [row async for row in manager.iter_observed()]
        failed_patient = [row for row in rows if row["patient_id"] == "P001"]
        unaffected_patient = [row for row in rows if row["patient_id"] == "P002"]
        self.assertEqual(len(failed_patient), 5)
        self.assertTrue(
            any(row["sensor_status"] == "DISCONNECTED" for row in failed_patient)
        )
        self.assertEqual(len(unaffected_patient), 5)
        self.assertTrue(
            all(row["sensor_connected"] for row in unaffected_patient)
        )

    async def test_adapter_rejects_every_ground_truth_field(self) -> None:
        adapter = StreamingAdapter(sink=lambda _: asyncio.sleep(0))
        for key in (
            "true_hr_bpm",
            "true_spo2_percent",
            "true_rr_bpm",
            "true_temp_c",
            "scenario_name",
            "scenario_event_id",
            "scenario_event_active",
            "event_onset_timestamp",
            "event_end_timestamp",
            "injected_physiological_changes_json",
            "sensor_fault_label",
        ):
            with self.subTest(field=key):
                with self.assertRaisesRegex(ValueError, "Ground-truth fields"):
                    await adapter.publish({"patient_id": "P001", key: "truth"})

    async def test_continuous_stream_stops_on_request(self) -> None:
        profiles = load_patients()
        config = SimulatorConfig(duration_seconds=1, realtime=True)
        manager = SimulationManager(
            config,
            profiles=profiles[:1],
            baseline=VitalBaseline(72, 97, 16, 36.8),
            noise=NoiseParameters(0, 0, 0, 0),
        )
        stop = asyncio.Event()
        stream = manager.iter_observed(stop_event=stop, continuous=True)
        first = await anext(stream)
        stop.set()
        remaining = [row async for row in stream]
        self.assertEqual(first["patient_id"], "P001")
        self.assertLessEqual(len(remaining), 1)


if __name__ == "__main__":
    unittest.main()
