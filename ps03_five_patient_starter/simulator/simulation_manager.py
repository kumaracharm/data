from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import (
    DATA,
    NoiseParameters,
    SimulatorConfig,
    VitalBaseline,
    load_patient_baseline,
    load_provisional_baseline,
)
from .ground_truth import ground_truth_record
from .patient_loader import PatientProfile, load_patients
from .physiological_engine import PhysiologicalEngine
from .scenario_engine import SCENARIOS, ScenarioEngine
from .sensor_model import SensorModel


OBSERVED_COLUMNS = [
    "event_id",
    "patient_id",
    "timestamp",
    "observed_hr_bpm",
    "observed_spo2_percent",
    "observed_rr_bpm",
    "observed_temp_c",
    "signal_quality",
    "sensor_status",
    "sensor_connected",
]
GROUND_TRUTH_COLUMNS = [
    "patient_id",
    "timestamp",
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
]
EVENT_NAMESPACE = uuid.UUID("cc8183ea-bfb0-44d8-9d6c-56f57c55a209")


@dataclass
class PatientState:
    patient_id: str
    heart_rate: float
    spo2: float
    respiratory_rate: float
    temperature: float
    baseline: VitalBaseline
    clinical_conditions: tuple[str, ...]
    historical_medication_context: tuple[str, ...]
    spo2_scale: int
    sensor_status: str
    active_scenario: str
    random_seed: int


class SimulationManager:
    """Runs isolated patient simulations and publishes only observed sensor records."""

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        *,
        profiles: list[PatientProfile] | None = None,
        baseline: VitalBaseline | None = None,
        noise: NoiseParameters | None = None,
        scenario_map: dict[str, str] | None = None,
        scenario_parameters: dict[str, dict[str, Any]] | None = None,
        sensor_parameters: dict[str, float] | None = None,
    ) -> None:
        self.config = config or SimulatorConfig()
        self.profiles = profiles or load_patients()
        self.baseline = baseline or load_provisional_baseline()
        self.baselines = {
            profile.patient_id: baseline or load_patient_baseline(profile.patient_id)
            for profile in self.profiles
        }
        self.noise = noise or NoiseParameters()
        self.scenario_map = scenario_map or {}
        scenario_config = json.loads(
            (DATA / "simulation" / "scenario_config.json").read_text(encoding="utf-8")
        )
        supplied_scenario_parameters = scenario_parameters or {}
        self.scenario_parameters: dict[str, dict[str, Any]] = {}
        for profile in self.profiles:
            scenario = self.scenario_map.get(profile.patient_id, "stable_recovery")
            defaults: dict[str, Any] = {
                "onset_fraction": scenario_config["default_onset_fraction"],
                "end_fraction": scenario_config["default_end_fraction"],
            }
            if scenario in {
                "gradual_deterioration",
                "sudden_deterioration",
                "deterioration_with_sensor_failure",
                "recovery_after_deterioration",
            }:
                defaults["physiological_changes"] = scenario_config[
                    "deterioration_physiological_changes"
                ]
            if scenario == "deterioration_with_sensor_failure":
                defaults["sensor_failure_fraction"] = scenario_config[
                    "deterioration_with_sensor_failure_fraction"
                ]
            defaults.update(supplied_scenario_parameters.get(profile.patient_id, {}))
            self.scenario_parameters[profile.patient_id] = defaults
        if sensor_parameters is None:
            sensor_config = json.loads(
                (DATA / "simulation" / "sensor_config.json").read_text(encoding="utf-8")
            )
            self.sensor_parameters = sensor_config
        else:
            self.sensor_parameters = sensor_parameters
        self.patient_states: dict[str, PatientState] = {}
        for profile in self.profiles:
            seed = self.config.seed + int(profile.patient_id[1:]) * 1009
            scenario = self.scenario_map.get(profile.patient_id, "stable_recovery")
            self._validate_scenario(scenario)
            self.patient_states[profile.patient_id] = PatientState(
                patient_id=profile.patient_id,
                heart_rate=self.baselines[profile.patient_id].hr_bpm,
                spo2=self.baselines[profile.patient_id].spo2_percent,
                respiratory_rate=self.baselines[profile.patient_id].rr_bpm,
                temperature=self.baselines[profile.patient_id].temp_c,
                baseline=self.baselines[profile.patient_id],
                clinical_conditions=profile.historical_conditions,
                historical_medication_context=profile.historical_prescription_labels,
                spo2_scale=profile.spo2_scale,
                sensor_status="INITIALIZED",
                active_scenario=scenario,
                random_seed=seed,
            )
        self._ground_truth_rows: dict[str, list[dict[str, Any]]] = {
            profile.patient_id: [] for profile in self.profiles
        }

    @staticmethod
    def _validate_scenario(scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario {scenario!r}; choose from {sorted(SCENARIOS)}")

    def _utc_timestamp(self, tick: int) -> str:
        timestamp = self.config.start_timestamp.astimezone(timezone.utc) + timedelta(
            seconds=tick * self.config.sampling_interval_seconds
        )
        return timestamp.isoformat().replace("+00:00", "Z")

    async def iter_observed(
        self,
        *,
        ticks: int | None = None,
        stop_event: asyncio.Event | None = None,
        continuous: bool = False,
    ):
        """Yield observed-only payloads; cancellation or stop_event ends continuous mode."""
        for rows in self._ground_truth_rows.values():
            rows.clear()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        finite_ticks = ticks if ticks is not None else self.config.tick_count
        if finite_ticks <= 0:
            raise ValueError("ticks must be positive")
        if ticks is not None and continuous:
            raise ValueError("Specify either finite ticks or continuous streaming, not both.")
        continuous = continuous or (ticks is None and stop_event is not None)

        async def patient_worker(profile: PatientProfile) -> None:
            state = self.patient_states[profile.patient_id]
            scenario = ScenarioEngine(
                profile.patient_id,
                state.active_scenario,
                state.random_seed,
                finite_ticks,
                self.scenario_parameters.get(profile.patient_id),
            )
            physiology = PhysiologicalEngine(
                self._patient_config(profile.patient_id, state), self.noise
            )
            sensor = SensorModel(
                state.random_seed + 1,
                self.noise,
                missing_probability=self.config.missing_probability,
                parameters=self.sensor_parameters,
            )
            tick = 0
            try:
                while continuous or tick < finite_ticks:
                    if stop_event is not None and stop_event.is_set():
                        break
                    step = scenario.step(tick)
                    truth = physiology.step(tick, step.physiological_changes)
                    timestamp = self._utc_timestamp(tick)
                    observation_id = str(
                        uuid.uuid5(
                            EVENT_NAMESPACE,
                            f"{profile.patient_id}:{self.config.seed}:{state.active_scenario}:{tick}",
                        )
                    )
                    observed = sensor.observe(
                        truth,
                        step,
                        timestamp=timestamp,
                        event_id=observation_id,
                        patient_id=profile.patient_id,
                    )
                    state.sensor_status = str(observed["sensor_status"])
                    ground_truth = ground_truth_record(
                        truth,
                        step,
                        timestamp=timestamp,
                        start_timestamp=self.config.start_timestamp,
                        sampling_interval_seconds=self.config.sampling_interval_seconds,
                        sensor_fault_label=step.sensor_fault_label,
                    )
                    self._ground_truth_rows[profile.patient_id].append(ground_truth)
                    await queue.put(("observation", observed))
                    tick += 1
                    if not self.config.realtime:
                        await asyncio.sleep(0)
                    if self.config.realtime:
                        delay = self.config.sampling_interval_seconds / self.config.speed
                        if stop_event is None:
                            await asyncio.sleep(delay)
                        else:
                            try:
                                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                            except TimeoutError:
                                pass
                await queue.put(("done", profile.patient_id))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await queue.put(("error", error))
                await queue.put(("done", profile.patient_id))

        tasks = [asyncio.create_task(patient_worker(profile)) for profile in self.profiles]
        completed = 0
        try:
            while completed < len(tasks):
                kind, payload = await queue.get()
                if kind == "done":
                    completed += 1
                elif kind == "error":
                    raise payload
                else:
                    yield payload
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _patient_config(self, patient_id: str, state: PatientState):
        from .config import PatientSimulationConfig

        return PatientSimulationConfig(
            patient_id=patient_id,
            seed=state.random_seed,
            baseline=state.baseline,
            sampling_interval_seconds=self.config.sampling_interval_seconds,
            scenario=state.active_scenario,
            scenario_parameters=self.scenario_parameters.get(patient_id, {}),
        )

    async def run_to_directory(
        self,
        output_dir: Path,
        *,
        adapter: Any | None = None,
        ticks: int | None = None,
    ) -> dict[str, Any]:
        observed_rows: dict[str, list[dict[str, Any]]] = {
            profile.patient_id: [] for profile in self.profiles
        }
        async for observation in self.iter_observed(ticks=ticks):
            patient_id = str(observation["patient_id"])
            observed_rows[patient_id].append(observation)
            if adapter is not None:
                await adapter.publish(observation)
        return self.write_outputs(output_dir, observed_rows)

    def write_outputs(
        self, output_dir: Path, observed_rows: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        observed_dir = output_dir / "observed"
        ground_truth_dir = output_dir / "ground_truth"
        manifest_dir = output_dir / "manifests"
        for folder in (observed_dir, ground_truth_dir, manifest_dir):
            folder.mkdir(parents=True, exist_ok=True)

        scenario_counts: dict[str, int] = {}
        initial_states: list[dict[str, Any]] = []
        for profile in self.profiles:
            patient_id = profile.patient_id
            state = self.patient_states[patient_id]
            observed = sorted(
                observed_rows[patient_id],
                key=lambda row: (row["timestamp"], row["event_id"]),
            )
            truth = sorted(
                self._ground_truth_rows[patient_id],
                key=lambda row: row["timestamp"],
            )
            pd.DataFrame(observed, columns=OBSERVED_COLUMNS).to_csv(
                observed_dir / f"{patient_id}.csv", index=False
            )
            pd.DataFrame(truth, columns=GROUND_TRUTH_COLUMNS).to_csv(
                ground_truth_dir / f"{patient_id}_ground_truth.csv", index=False
            )
            scenario_counts[self.patient_states[patient_id].active_scenario] = (
                scenario_counts.get(self.patient_states[patient_id].active_scenario, 0) + 1
            )
            initial_states.append(
                {
                    "patient_id": patient_id,
                    "historical_clinical_conditions": {
                        "values": list(profile.historical_conditions),
                        "provenance": "SYNTHEA_PROFILE_HISTORICAL_NOT_CONFIRMED_ACTIVE",
                    },
                    "historical_medication_prescriptions": {
                        "values": list(profile.historical_prescription_labels),
                        "provenance": "HISTORICAL_PRESCRIPTIONS_CURRENT_USE_UNCONFIRMED",
                    },
                    "historical_vital_observations_before_encounter_end": list(
                        profile.historical_observations
                    ),
                    "initial_simulated_baseline": {
                        **state.baseline.as_dict(),
                        "provenance": state.baseline.provenance,
                        "patient_specific_configuration": (
                            state.baseline.provenance
                            == "PROVISIONAL_PATIENT_KEYED_DEMO_OVERRIDE_NOT_CLINICAL"
                        ),
                        "clinical_baseline_verified": False,
                    },
                    "spo2_scale": state.spo2_scale,
                    "sensor_status_at_initialization": "INITIALIZED",
                    "active_scenario": state.active_scenario,
                    "random_seed": state.random_seed,
                }
            )
        (manifest_dir / "patient_initial_states.json").write_text(
            json.dumps(initial_states, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "patient_count": len(self.profiles),
            "patient_ids": [profile.patient_id for profile in self.profiles],
            "concurrent_patient_streams": len(self.profiles),
            "sampling_interval_seconds": self.config.sampling_interval_seconds,
            "speed_multiplier": self.config.speed,
            "simulated_ticks_per_patient": {
                patient_id: len(rows) for patient_id, rows in observed_rows.items()
            },
            "scenarios": {
                patient_id: self.patient_states[patient_id].active_scenario
                for patient_id in self.patient_states
            },
            "scenario_patient_counts": scenario_counts,
            "baseline_configuration": self.baseline.provenance,
            "patient_keyed_demo_overrides_configured": any(
                baseline.provenance
                == "PROVISIONAL_PATIENT_KEYED_DEMO_OVERRIDE_NOT_CLINICAL"
                for baseline in self.baselines.values()
            ),
            "historical_measurements_used_as_initial_baselines": False,
            "clinical_validation": False,
            "observed_and_ground_truth_are_separate": True,
            "observed_payload_schema": "data/simulation/gateway_observation.schema.json",
        }
        (manifest_dir / "simulation_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return manifest


async def replay_to_csv(
    recording_id: str,
    output_path: Path,
    *,
    speed: float = 1.0,
    realtime: bool = False,
) -> int:
    from .bidmc_loader import iter_bidmc_replay_async

    if speed <= 0:
        raise ValueError("speed must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    async for row in iter_bidmc_replay_async(
        recording_id, speed=speed, realtime=realtime
    ):
        frame = pd.DataFrame([row])
        frame.to_csv(
            output_path,
            mode="w" if count == 0 else "a",
            header=count == 0,
            index=False,
        )
        count += 1
    return count


def cohort_data_root() -> Path:
    return DATA
