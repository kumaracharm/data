from __future__ import annotations

import asyncio
import json
from pathlib import Path
from collections.abc import AsyncIterator, Iterator

import pandas as pd

from .config import DATA


RECORDINGS = {
    f"bidmc{number:02d}": f"bidmc_{number:02d}_Numerics.csv"
    for number in range(1, 6)
}


def load_bidmc_recording(
    recording_id: str, data_dir: Path | None = None
) -> pd.DataFrame:
    if recording_id not in RECORDINGS:
        raise ValueError(f"Unknown BIDMC recording {recording_id!r}")
    folder = (data_dir or DATA) / "bidmc_original"
    source_path = folder / RECORDINGS[recording_id]
    mapping_file = folder / "header_mapping.json"
    if not source_path.is_file() or not mapping_file.is_file():
        raise FileNotFoundError(f"Missing original BIDMC source or mapping for {recording_id}")
    mappings = json.loads(mapping_file.read_text(encoding="utf-8"))
    mapped = mappings[f"P{int(recording_id[-2:]):03d}"]["mapped_columns"]
    frame = pd.read_csv(source_path)
    missing = [column for column in mapped.values() if column not in frame.columns]
    if missing:
        raise ValueError(f"{recording_id}: mapped source headers are missing: {missing}")
    result = pd.DataFrame(
        {
            "recording_id": recording_id,
            "source_time_s": pd.to_numeric(frame[mapped["time_s"]], errors="coerce"),
            "hr_bpm": pd.to_numeric(frame[mapped["hr_bpm"]], errors="coerce"),
            "spo2_percent": pd.to_numeric(frame[mapped["spo2_percent"]], errors="coerce"),
            "rr_bpm": pd.to_numeric(frame[mapped["rr_bpm"]], errors="coerce"),
        }
    )
    result["temp_c"] = float("nan")
    result["temperature_status"] = "UNAVAILABLE_IN_BIDMC_NUMERICS"
    return result


def iter_bidmc_replay(
    recording_id: str,
    *,
    speed: float = 1.0,
    data_dir: Path | None = None,
) -> Iterator[dict[str, object]]:
    if speed <= 0:
        raise ValueError("Replay speed must be positive")
    for row in load_bidmc_recording(recording_id, data_dir).itertuples(index=False):
        yield {
            "recording_id": row.recording_id,
            "source_time_s": row.source_time_s,
            "hr_bpm": None if pd.isna(row.hr_bpm) else float(row.hr_bpm),
            "spo2_percent": None if pd.isna(row.spo2_percent) else float(row.spo2_percent),
            "rr_bpm": None if pd.isna(row.rr_bpm) else float(row.rr_bpm),
            "temp_c": None,
            "temperature_status": row.temperature_status,
        }


async def iter_bidmc_replay_async(
    recording_id: str,
    *,
    speed: float = 1.0,
    realtime: bool = True,
    data_dir: Path | None = None,
) -> AsyncIterator[dict[str, object]]:
    if speed <= 0:
        raise ValueError("Replay speed must be positive")
    previous_source_time: float | None = None
    for row in iter_bidmc_replay(recording_id, speed=speed, data_dir=data_dir):
        source_time = float(row["source_time_s"])
        if realtime and previous_source_time is not None:
            await asyncio.sleep(max(0.0, source_time - previous_source_time) / speed)
        previous_source_time = source_time
        yield row
