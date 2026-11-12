from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.bidmc_analysis import analyze_bidmc
from simulator.config import NoiseParameters, SimulatorConfig, load_provisional_baseline
from simulator.simulation_manager import OBSERVED_COLUMNS


CHANNELS = ("hr_bpm", "spo2_percent", "rr_bpm", "temp_c")
OBSERVED_CHANNELS = (
    "observed_hr_bpm",
    "observed_spo2_percent",
    "observed_rr_bpm",
    "observed_temp_c",
)
PATIENT_IDS = tuple(f"P{number:03d}" for number in range(1, 11))
PHYSIOLOGY_AR_COEFFICIENT = 0.65
UTC_START = SimulatorConfig().start_timestamp


def _noise_values(noise: NoiseParameters) -> np.ndarray:
    return np.array(
        [
            noise.hr_bpm_sd,
            noise.spo2_percent_sd,
            noise.rr_bpm_sd,
            noise.temp_c_sd,
        ],
        dtype=np.float64,
    )


def _patient_frames(
    patient_id: str,
    *,
    ticks: int,
    samples_per_second: int,
    chunk_rows: int,
    seed: int,
    baseline: dict[str, float],
    noise: NoiseParameters,
) -> Iterator[pd.DataFrame]:
    patient_number = int(patient_id[1:])
    physiology_rng = np.random.default_rng(seed + patient_number * 1009)
    sensor_rng = np.random.default_rng(seed + patient_number * 1009 + 1)
    noise_sd = _noise_values(noise)
    baseline_values = np.array([baseline[channel] for channel in CHANNELS])
    lower_bounds = np.array([25.0, 50.0, 3.0, 30.0])
    upper_bounds = np.array([240.0, 100.0, 70.0, 43.0])
    previous_noise = np.zeros(len(CHANNELS), dtype=np.float64)
    interval = pd.Timedelta(seconds=1 / samples_per_second)

    for first_tick in range(0, ticks, chunk_rows):
        row_count = min(chunk_rows, ticks - first_tick)
        innovations = physiology_rng.normal(
            0.0, noise_sd * 0.5, size=(row_count, len(CHANNELS))
        )
        ewm_inputs = np.vstack(
            (previous_noise, innovations / (1.0 - PHYSIOLOGY_AR_COEFFICIENT))
        )
        physiological_noise = (
            pd.DataFrame(ewm_inputs)
            .ewm(alpha=1.0 - PHYSIOLOGY_AR_COEFFICIENT, adjust=False)
            .mean()
            .to_numpy()[1:]
        )
        previous_noise = physiological_noise[-1].copy()
        sensor_noise = sensor_rng.normal(
            0.0, noise_sd, size=(row_count, len(CHANNELS))
        )
        observed_values = np.clip(
            baseline_values + physiological_noise + sensor_noise,
            lower_bounds,
            upper_bounds,
        )
        observed_values = np.round(observed_values, 4)

        tick_values = np.arange(
            first_tick, first_tick + row_count, dtype=np.int64
        )
        timestamps = pd.date_range(
            start=UTC_START + timedelta(seconds=first_tick / samples_per_second),
            periods=row_count,
            freq=interval,
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        event_ids = np.char.add(
            f"{patient_id}-", np.char.zfill(tick_values.astype(str), 8)
        )

        frame_data: dict[str, object] = {
            "event_id": event_ids,
            "patient_id": patient_id,
            "timestamp": timestamps,
        }
        for column_index, column in enumerate(OBSERVED_CHANNELS):
            frame_data[column] = observed_values[:, column_index]
        frame_data.update(
            {
                "signal_quality": round(
                    float(np.clip(1.0 - np.abs(noise_sd).sum() / 30.0, 0.2, 1.0)),
                    4,
                ),
                "sensor_status": "CONNECTED",
                "sensor_connected": True,
            }
        )
        yield pd.DataFrame(frame_data, columns=OBSERVED_COLUMNS)


def _write_patient_csv(
    patient_id: str,
    output: Path,
    *,
    ticks: int,
    samples_per_second: int,
    chunk_rows: int,
    seed: int,
    baseline: dict[str, float],
    noise: NoiseParameters,
) -> None:
    with output.open("w", encoding="utf-8", newline="") as stream:
        for chunk_index, frame in enumerate(
            _patient_frames(
                patient_id,
                ticks=ticks,
                samples_per_second=samples_per_second,
                chunk_rows=chunk_rows,
                seed=seed,
                baseline=baseline,
                noise=noise,
            )
        ):
            frame.to_csv(
                stream,
                index=False,
                header=chunk_index == 0,
                float_format="%.4f",
                na_rep="",
            )


def export_dataset(
    output: Path,
    *,
    duration_days: int = 15,
    samples_per_second: int = 10,
    seed: int = 20261112,
    chunk_rows: int = 250_000,
    force: bool = False,
) -> dict[str, object]:
    if duration_days <= 0:
        raise ValueError("duration_days must be positive")
    if samples_per_second <= 0 or 1_000_000_000 % samples_per_second:
        raise ValueError("samples_per_second must divide one billion nanoseconds")
    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be positive")
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}; pass --force to replace it.")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial_output = output.with_name(output.name + ".partial")
    if partial_output.exists():
        raise FileExistsError(f"Temporary output already exists: {partial_output}")
    work_dir = output.with_name(output.name + ".work")
    if work_dir.exists():
        raise FileExistsError(f"Temporary work directory already exists: {work_dir}")

    ticks_per_patient = duration_days * 24 * 60 * 60 * samples_per_second
    analysis = analyze_bidmc()
    noise = NoiseParameters.from_analysis(analysis)
    baseline = load_provisional_baseline()
    baseline_values = baseline.as_dict()
    interval_seconds = 1.0 / samples_per_second
    end_timestamp = UTC_START + timedelta(
        seconds=(ticks_per_patient - 1) * interval_seconds
    )
    manifest: dict[str, object] = {
        "dataset_name": "PS-03 synthetic 10-patient wearable observations",
        "data_type": "synthetic_observed_sensor_readings",
        "patient_ids": list(PATIENT_IDS),
        "patient_count": len(PATIENT_IDS),
        "duration_days": duration_days,
        "sampling_rate_per_patient_hz": samples_per_second,
        "sampling_interval_seconds": interval_seconds,
        "rows_per_patient": ticks_per_patient,
        "total_rows": ticks_per_patient * len(PATIENT_IDS),
        "start_timestamp_utc": UTC_START.isoformat().replace("+00:00", "Z"),
        "end_timestamp_utc": end_timestamp.isoformat().replace("+00:00", "Z"),
        "scenario": "stable_recovery",
        "seed": seed,
        "baseline_values": baseline_values,
        "baseline_provenance": baseline.provenance,
        "noise_parameters": {
            "hr_bpm_sd": noise.hr_bpm_sd,
            "spo2_percent_sd": noise.spo2_percent_sd,
            "rr_bpm_sd": noise.rr_bpm_sd,
            "temp_c_sd": noise.temp_c_sd,
        },
        "noise_source": "BIDMC short-term variability calibration configured by this project",
        "observed_columns": OBSERVED_COLUMNS,
        "ground_truth_included": False,
        "clinical_validation": False,
        "not_medical_advice": True,
    }
    readme = (
        "PS-03 synthetic wearable dataset\n"
        "================================\n"
        "This archive contains synthetic, observed-style sensor data for 10 demo "
        "patient IDs over 15 days at 10 samples per second per patient. It has "
        "129,600,000 total rows. Each observed/Pxxx.csv contains one row per "
        "100 ms with the columns listed in manifest.json.\n\n"
        "The common starting values are provisional demo values, not verified "
        "patient baselines. Random variation uses the project's BIDMC-derived "
        "short-term noise settings. All readings are generated synthetic data; "
        "no historical patient measurements are included. Ground truth is not "
        "included. This dataset is not clinically validated, diagnostic, or "
        "medical advice.\n"
    )

    work_dir.mkdir()
    try:
        workers = min(len(PATIENT_IDS), os.cpu_count() or 1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _write_patient_csv,
                    patient_id,
                    work_dir / f"{patient_id}.csv",
                    ticks=ticks_per_patient,
                    samples_per_second=samples_per_second,
                    chunk_rows=chunk_rows,
                    seed=seed,
                    baseline=baseline_values,
                    noise=noise,
                ): patient_id
                for patient_id in PATIENT_IDS
            }
            for completed, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                patient_id = futures[future]
                future.result()
                print(
                    f"Completed {patient_id} ({completed}/{len(PATIENT_IDS)}): "
                    f"{ticks_per_patient:,} rows"
                )

        with zipfile.ZipFile(
            partial_output,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            archive.writestr("README.txt", readme)
            for patient_id in PATIENT_IDS:
                archive.write(
                    work_dir / f"{patient_id}.csv",
                    f"observed/{patient_id}.csv",
                )
        partial_output.replace(output)
    finally:
        if partial_output.exists():
            partial_output.unlink()
        if work_dir.exists():
            shutil.rmtree(work_dir)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream a long-duration synthetic wearable dataset into a CSV ZIP."
    )
    parser.add_argument("--output", type=Path, required=True, help="Destination ZIP path.")
    parser.add_argument("--duration-days", type=int, default=15)
    parser.add_argument("--samples-per-second", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    parser.add_argument("--force", action="store_true", help="Replace an existing ZIP.")
    args = parser.parse_args()
    manifest = export_dataset(
        args.output,
        duration_days=args.duration_days,
        samples_per_second=args.samples_per_second,
        seed=args.seed,
        chunk_rows=args.chunk_rows,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "patient_count": manifest["patient_count"],
                "rows_per_patient": manifest["rows_per_patient"],
                "total_rows": manifest["total_rows"],
                "sampling_rate_per_patient_hz": manifest[
                    "sampling_rate_per_patient_hz"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
