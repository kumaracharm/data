from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .bidmc_loader import RECORDINGS, load_bidmc_recording
from .config import DATA


CHANNELS = ("hr_bpm", "spo2_percent", "rr_bpm")


def _channel_statistics(
    values: pd.Series, source_time: pd.Series | None = None
) -> dict[str, float | int | None]:
    clean = values.dropna().astype(float)
    if clean.empty:
        return {
            "count": 0,
            "missing_percent": 100.0,
            "mean": None,
            "std": None,
            "median": None,
            "q1": None,
            "q3": None,
            "iqr": None,
            "short_term_difference_std": None,
        }
    q1, q3 = clean.quantile([0.25, 0.75])
    differences = values.diff()
    contiguous = values.notna() & values.shift().notna()
    if source_time is not None:
        contiguous &= source_time.diff().eq(1.0)
    short_term_differences = differences[contiguous].dropna()
    return {
        "count": int(clean.size),
        "missing_percent": float(values.isna().mean() * 100),
        "mean": float(clean.mean()),
        "std": float(clean.std(ddof=1)) if clean.size > 1 else 0.0,
        "median": float(clean.median()),
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(q3 - q1),
        "short_term_difference_std": (
            float(short_term_differences.std(ddof=1))
            if short_term_differences.size > 1
            else 0.0
        ),
    }


def analyze_recording(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "recording_id": str(frame["recording_id"].iloc[0]),
        "rows": int(len(frame)),
        "sampling_interval_seconds": {},
        "channels": {},
        "pairwise_signal_correlations": {},
    }
    time_deltas = frame["source_time_s"].diff().dropna()
    result["sampling_interval_seconds"] = {
        "median": float(time_deltas.median()) if not time_deltas.empty else None,
        "mean": float(time_deltas.mean()) if not time_deltas.empty else None,
        "irregular_interval_count": int((time_deltas != time_deltas.median()).sum())
        if not time_deltas.empty
        else 0,
    }
    for channel in CHANNELS:
        result["channels"][channel] = _channel_statistics(
            frame[channel], frame["source_time_s"]
        )
    result["pairwise_signal_correlations"] = frame[list(CHANNELS)].corr(
        min_periods=3
    ).to_dict()
    return result


def analyze_bidmc(data_dir: Path | None = None) -> dict[str, Any]:
    frames = [load_bidmc_recording(recording, data_dir) for recording in RECORDINGS]
    per_recording = [analyze_recording(frame) for frame in frames]
    combined = pd.concat(frames, ignore_index=True)
    pooled_channels = {}
    for channel in CHANNELS:
        statistics = _channel_statistics(combined[channel])
        differences = pd.concat(
            [
                frame[channel]
                .diff()
                .where(frame["source_time_s"].diff().eq(1.0))
                .dropna()
                for frame in frames
            ],
            ignore_index=True,
        )
        statistics["short_term_difference_std"] = (
            float(differences.std(ddof=1)) if differences.size > 1 else 0.0
        )
        pooled_channels[channel] = statistics
    return {
        "dataset": "BIDMC PPG and Respiration Dataset v1.0.0",
        "source_url": "https://physionet.org/content/bidmc/1.0.0/",
        "recording_count": len(per_recording),
        "recordings": per_recording,
        "pooled": {
            "rows": int(len(combined)),
            "channels": pooled_channels,
            "short_term_difference_std": {
                channel: pooled_channels[channel]["short_term_difference_std"]
                for channel in CHANNELS
            },
            "pairwise_signal_correlations": combined[list(CHANNELS)]
            .corr(min_periods=3)
            .to_dict(),
        },
        "interpretation_note": (
            "ICU recordings describe observed signal variability, not stable "
            "post-discharge physiology. Only short-term variability statistics are "
            "used to parameterize simulation noise; absolute recording values are "
            "never used as patient baselines."
        ),
    }


def write_analysis(output_dir: Path | None = None) -> Path:
    destination = output_dir or DATA / "bidmc_analysis"
    destination.mkdir(parents=True, exist_ok=True)
    result = analyze_bidmc()
    report_path = destination / "bidmc_variability.json"
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    pd.json_normalize(result["recordings"], sep=".").to_csv(
        destination / "bidmc_recording_summary.csv", index=False
    )
    return report_path


if __name__ == "__main__":
    print(write_analysis())
