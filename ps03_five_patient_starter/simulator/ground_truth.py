from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .scenario_engine import ScenarioStep


def ground_truth_record(
    truth: dict[str, Any],
    step: ScenarioStep,
    *,
    timestamp: str,
    start_timestamp: datetime,
    sampling_interval_seconds: float,
    sensor_fault_label: str | None,
) -> dict[str, Any]:
    onset = (
        start_timestamp + timedelta(seconds=step.onset_tick * sampling_interval_seconds)
        if step.onset_tick is not None
        else None
    )
    end = (
        start_timestamp + timedelta(seconds=step.end_tick * sampling_interval_seconds)
        if step.end_tick is not None
        else None
    )
    return {
        "patient_id": truth["patient_id"],
        "timestamp": timestamp,
        "true_hr_bpm": truth["true_hr_bpm"],
        "true_spo2_percent": truth["true_spo2_percent"],
        "true_rr_bpm": truth["true_rr_bpm"],
        "true_temp_c": truth["true_temp_c"],
        "scenario_name": step.name,
        "scenario_event_id": step.event_id,
        "scenario_event_active": step.active,
        "event_onset_timestamp": onset.isoformat().replace("+00:00", "Z")
        if onset
        else None,
        "event_end_timestamp": end.isoformat().replace("+00:00", "Z") if end else None,
        "injected_physiological_changes_json": json.dumps(
            step.physiological_changes, sort_keys=True
        ),
        "sensor_fault_label": sensor_fault_label,
    }
