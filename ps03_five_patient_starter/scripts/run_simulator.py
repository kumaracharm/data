from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.bidmc_analysis import analyze_bidmc, write_analysis
from simulator.bidmc_loader import RECORDINGS
from simulator.config import DATA, NoiseParameters, SimulatorConfig
from simulator.patient_loader import load_patients
from simulator.simulation_manager import SimulationManager, replay_to_csv
from simulator.streaming_adapter import StreamingAdapter


def _read_scenario_map(path: Path | None, default_scenario: str) -> dict[str, str]:
    if path is None:
        return {f"P{number:03d}": default_scenario for number in range(1, 11)}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("scenarios", data)
    if not isinstance(mapping, dict):
        raise ValueError("Scenario map JSON must be a patient-ID-to-scenario object.")
    return {str(patient_id): str(scenario) for patient_id, scenario in mapping.items()}


async def _run(args: argparse.Namespace) -> None:
    if args.mode == "replay":
        if args.recording == "all":
            output_dir = args.output or DATA / "simulation" / "replay"
            output_dir.mkdir(parents=True, exist_ok=True)
            replay_results = {}
            for recording_id in RECORDINGS:
                output_path = output_dir / f"{recording_id}.csv"
                replay_results[recording_id] = await replay_to_csv(
                    recording_id,
                    output_path,
                    speed=args.speed,
                    realtime=args.realtime,
                )
            replay_manifest = {
                "mode": "BIDMC_REAL_DATA_REPLAY",
                "recording_count": len(replay_results),
                "recordings": replay_results,
                "source_values_preserved": True,
                "synthetic_patient_id_added": False,
                "temperature": "UNAVAILABLE_IN_BIDMC_NUMERICS",
                "speed_multiplier": args.speed,
                "realtime": args.realtime,
            }
            (output_dir / "replay_manifest.json").write_text(
                json.dumps(replay_manifest, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"output": str(output_dir), **replay_manifest}, indent=2))
            return
        if args.recording not in RECORDINGS:
            raise ValueError(f"--recording must be one of {sorted(RECORDINGS)}")
        output = args.output or DATA / "simulation" / "replay" / f"{args.recording}.csv"
        count = await replay_to_csv(
            args.recording,
            output,
            speed=args.speed,
            realtime=args.realtime,
        )
        print(
            json.dumps(
                {
                    "mode": "BIDMC_REAL_DATA_REPLAY",
                    "recording_id": args.recording,
                    "rows": count,
                    "source_values_preserved": True,
                    "synthetic_patient_id_added": False,
                    "temperature": "UNAVAILABLE_IN_BIDMC_NUMERICS",
                    "speed_multiplier": args.speed,
                    "output": str(output),
                },
                indent=2,
            )
        )
        return

    profiles = load_patients()
    analysis = analyze_bidmc()
    write_analysis()
    noise = NoiseParameters.from_analysis(analysis)
    sensor_config = json.loads(
        (DATA / "simulation" / "sensor_config.json").read_text(encoding="utf-8")
    )
    scenarios = _read_scenario_map(args.scenario_map, args.scenario)
    unknown_patients = set(scenarios) - {profile.patient_id for profile in profiles}
    if unknown_patients:
        raise ValueError(f"Scenario map contains unknown patient IDs: {sorted(unknown_patients)}")
    config = SimulatorConfig(
        seed=args.seed,
        duration_seconds=args.duration,
        sampling_interval_seconds=args.sample_interval,
        speed=args.speed,
        realtime=args.realtime,
        gateway_url=args.gateway_url,
        missing_probability=(
            args.missing_probability
            if args.missing_probability is not None
            else float(sensor_config["missing_probability"])
        ),
    )
    manager = SimulationManager(
        config,
        profiles=profiles,
        noise=noise,
        scenario_map=scenarios,
    )
    output = args.output or DATA / "simulation" / "runs" / (
        Path(args.scenario_map).stem if args.scenario_map else args.scenario
    )
    adapter = StreamingAdapter(gateway_url=args.gateway_url) if args.gateway_url else None
    manifest = await manager.run_to_directory(output, adapter=adapter)
    manifest["bidmc_variability_source"] = "data/bidmc_analysis/bidmc_variability.json"
    manifest["gateway_url_configured"] = bool(args.gateway_url)
    manifest["gateway_contract_status"] = (
        "NOT_FINALIZED; provisional schema only" if args.gateway_url else "not configured"
    )
    manifest["initial_values_configuration"] = (
        "data/simulation/initial_values.json; identical provisional demo values, not clinical baselines"
    )
    (output / "manifests" / "simulation_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **manifest}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="BIDMC replay or ten-patient virtual wearable simulation.")
    parser.add_argument("--mode", choices=("replay", "simulation"), default="simulation")
    parser.add_argument(
        "--recording", choices=["all", *sorted(RECORDINGS)], default="all"
    )
    parser.add_argument("--scenario", default="stable_recovery")
    parser.add_argument("--scenario-map", type=Path)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--speed", type=float, default=1.0, help="Realtime replay/simulation speed multiplier.")
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--missing-probability", type=float)
    parser.add_argument("--realtime", action="store_true", help="Pace output in wall-clock time.")
    parser.add_argument("--gateway-url", help="Optional provisional JSON POST endpoint; no credentials are embedded.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
