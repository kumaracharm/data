from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BIDMC_DIR = DATA / "bidmc_original"
SYNTHEA_DIR = DATA / "synthea_original"
PATIENTS_DIR = DATA / "patients"
PROFILE_DIR = DATA / "profiles"
START_TIMESTAMP = pd.Timestamp("2026-01-01T00:00:00Z")
SCENARIOS: dict[str, dict[str, Any]] = {
    "P001": {
        "scenario": "STABLE_POST_DISCHARGE_RECOVERY",
        "description": (
            "Stable post-discharge recovery simulation over an observed BIDMC "
            "recording. The post-discharge label is a locally defined scenario, "
            "not a Synthea encounter or BIDMC patient attribute."
        ),
    },
    "P002": {
        "scenario": "COPD_HYPERCAPNIC_SPO2_TARGET_TEST",
        "description": (
            "Synthetic COPD/hypercapnic respiratory condition profile with a "
            "configured SpO2 target-range test."
        ),
        "locally_defined_labels": [
            {
                "label": "HYPERCAPNIC_RESPIRATORY_SCENARIO",
                "origin": (
                    "Local simulation label only; no hypercapnia or respiratory-failure "
                    "Synthea Condition is claimed unless explicitly present in the FHIR resources."
                ),
            }
        ],
        "simulation_parameter": {
            "name": "SpO2 test target range",
            "range_percent": [88, 92],
            "not_medical_advice": True,
            "parameter_is_simulation_only": True,
        },
    },
    "P003": {
        "scenario": "HEART_FAILURE_GRADUAL_MULTI_VITAL_DRIFT",
        "description": "Synthetic heart failure profile with gradual multi-vital drift.",
    },
    "P004": {
        "scenario": "POST_PNEUMONIA_TRANSIENT_MOTION_ARTIFACT",
        "description": "Synthetic post-pneumonia profile with transient motion artifacts.",
    },
    "P005": {
        "scenario": "POST_SEPSIS_PROBE_REMOVAL_AND_PACKET_DROPOUT",
        "description": (
            "Synthetic post-sepsis profile with simulated probe-removal-like "
            "signal loss and a separate packet-dropout interval."
        ),
    },
}
RECORDINGS = {
    "P001": "01",
    "P002": "02",
    "P003": "03",
    "P004": "04",
    "P005": "05",
}
VITALS = ("hr_bpm", "rr_bpm", "spo2_percent")
OUTPUT_COLUMNS = [
    "timestamp",
    "patient_id",
    "bidmc_recording",
    "source_filename",
    "source_time_s",
    "hr_bpm_source",
    "rr_bpm_source",
    "spo2_percent_source",
    "hr_bpm_scenario_delta",
    "rr_bpm_scenario_delta",
    "spo2_percent_scenario_delta",
    "temp_c_synthetic",
    "hr_bpm",
    "rr_bpm",
    "spo2_percent",
    "hr_bpm_missing_reason",
    "rr_bpm_missing_reason",
    "spo2_percent_missing_reason",
    "temp_c_missing_reason",
    "temperature_provenance",
    "event_label",
    "is_injected",
]
EVENT_COLUMNS = [
    "event_id",
    "patient_id",
    "event_label",
    "start_timestamp",
    "end_timestamp",
    "start_index",
    "end_index",
    "event_description",
    "simulation_only",
    "affected_channels",
]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read required JSON {path}: {error}") from error


def read_source(recording: str) -> tuple[pd.DataFrame, dict[str, str], str]:
    mapping_file = load_json(BIDMC_DIR / "header_mapping.json")
    patient_id = next(
        (key for key, value in mapping_file.items() if value["recording_id"] == f"bidmc{recording}"),
        None,
    )
    if patient_id is None:
        raise ValueError(f"No recorded header mapping for BIDMC recording {recording}")

    source_path = BIDMC_DIR / f"bidmc_{recording}_Numerics.csv"
    if not source_path.exists():
        raise FileNotFoundError(f"Missing original BIDMC source file: {source_path}")
    frame = pd.read_csv(source_path)
    mapping = mapping_file[patient_id]["mapped_columns"]
    required_keys = {"time_s", "hr_bpm", "rr_bpm", "spo2_percent"}
    if not required_keys.issubset(mapping):
        raise ValueError(f"Incomplete BIDMC header mapping for {patient_id}: {mapping}")
    return frame, mapping, source_path.name


def numeric_source(frame: pd.DataFrame, source_header: str, patient_id: str) -> pd.Series:
    if source_header not in frame.columns:
        raise ValueError(f"{patient_id}: source header {source_header!r} is absent")
    raw = frame[source_header]
    values = pd.to_numeric(raw, errors="coerce")
    invalid = raw.notna() & raw.astype(str).str.strip().ne("") & values.isna()
    if invalid.any():
        row_numbers = invalid[invalid].index[:5].tolist()
        raise ValueError(f"{patient_id}: nonnumeric values in {source_header!r} at rows {row_numbers}")
    return values.astype(float)


def event_record(
    patient_id: str,
    label: str,
    start: int,
    end: int,
    timestamps: pd.Series,
    description: str,
    affected_channels: str,
) -> dict[str, Any]:
    return {
        "event_id": f"{patient_id}-E{start:04d}",
        "patient_id": patient_id,
        "event_label": label,
        "start_timestamp": timestamps.iloc[start],
        "end_timestamp": timestamps.iloc[end],
        "start_index": start,
        "end_index": end,
        "event_description": description,
        "simulation_only": True,
        "affected_channels": affected_channels,
    }


def make_patient_stream(
    patient_id: str,
    source: pd.DataFrame,
    mapping: dict[str, str],
    filename: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    count = len(source)
    if count == 0:
        raise ValueError(f"{patient_id}: source recording contains no rows")
    source_time = numeric_source(source, mapping["time_s"], patient_id)
    if source_time.isna().any() or source_time.duplicated().any() or not source_time.is_monotonic_increasing:
        raise ValueError(f"{patient_id}: source time values must be present and strictly increasing")

    original = {
        vital: numeric_source(source, mapping[vital], patient_id)
        for vital in VITALS
    }
    timestamps = (START_TIMESTAMP + pd.to_timedelta(source_time, unit="s")).map(
        lambda value: value.isoformat().replace("+00:00", "Z")
    )
    seed = int(patient_id[1:]) * 1009
    rng = np.random.default_rng(seed)
    temperature = 36.8 + rng.normal(0.0, 0.05, count)
    deltas = {vital: pd.Series(np.zeros(count), dtype=float) for vital in VITALS}
    final = {vital: values.copy() for vital, values in original.items()}
    missing_reasons = {
        vital: pd.Series(
            np.where(values.isna(), "SOURCE_READING_MISSING", ""), dtype="object"
        )
        for vital, values in original.items()
    }
    temperature_missing_reason = pd.Series([""] * count, dtype="object")
    labels = pd.Series(["NONE"] * count, dtype="object")
    injected = pd.Series(np.zeros(count, dtype=bool))
    events: list[dict[str, Any]] = []
    if patient_id == "P002":
        target = 90.0
        deltas["spo2_percent"] = target - original["spo2_percent"]
        final["spo2_percent"] = original["spo2_percent"] + deltas["spo2_percent"]
        labels[:] = "COPD_SPO2_TARGET_RANGE_TEST"
        injected[:] = True
        events.append(
            event_record(
                patient_id,
                "COPD_SPO2_TARGET_RANGE_TEST",
                0,
                count - 1,
                timestamps,
                "SpO2 values set to the 90% simulation midpoint; this is a test parameter, not medical advice.",
                "spo2_percent",
            )
        )
    elif patient_id == "P003":
        ramp = np.linspace(0.0, 1.0, count)
        deltas["hr_bpm"] = 18.0 * ramp
        deltas["rr_bpm"] = 8.0 * ramp
        deltas["spo2_percent"] = -5.0 * ramp
        temperature += 0.8 * ramp
        for vital in VITALS:
            final[vital] = original[vital] + deltas[vital]
        labels[:] = "GRADUAL_MULTI_VITAL_DRIFT"
        injected[:] = True
        events.append(
            event_record(
                patient_id,
                "GRADUAL_MULTI_VITAL_DRIFT",
                0,
                count - 1,
                timestamps,
                "Linear synthetic drift deltas increase across the complete source recording.",
                "hr_bpm;rr_bpm;spo2_percent;temp_c_synthetic",
            )
        )
    elif patient_id == "P004":
        window = min(5, max(1, count // 20))
        starts = sorted({int(count * fraction) for fraction in (0.2, 0.5, 0.8)})
        starts = [min(start, count - window) for start in starts]
        for event_number, start in enumerate(starts, start=1):
            end = start + window - 1
            sign = 1.0 if event_number % 2 else -1.0
            deltas["hr_bpm"].iloc[start : end + 1] = 24.0 * sign
            deltas["rr_bpm"].iloc[start : end + 1] = 6.0
            deltas["spo2_percent"].iloc[start : end + 1] = -7.0
            for vital in VITALS:
                final[vital].iloc[start : end + 1] = (
                    original[vital].iloc[start : end + 1]
                    + deltas[vital].iloc[start : end + 1]
                )
            labels.iloc[start : end + 1] = "MOTION_ARTIFACT_SIMULATED"
            injected.iloc[start : end + 1] = True
            events.append(
                event_record(
                    patient_id,
                    f"MOTION_ARTIFACT_SIMULATED_{event_number}",
                    start,
                    end,
                    timestamps,
                    "Transient signal perturbation added for the motion-artifact simulation.",
                    "hr_bpm;rr_bpm;spo2_percent",
                )
            )
    elif patient_id == "P005":
        probe_start = int(count * 0.60)
        probe_end = min(count - 1, probe_start + max(1, int(count * 0.08)) - 1)
        labels.iloc[probe_start : probe_end + 1] = "PROBE_REMOVAL_SIMULATED"
        injected.iloc[probe_start : probe_end + 1] = True
        for vital in ("hr_bpm", "spo2_percent"):
            final[vital].iloc[probe_start : probe_end + 1] = np.nan
            missing_reasons[vital].iloc[probe_start : probe_end + 1] = "SIMULATED_PROBE_REMOVAL"
        events.append(
            event_record(
                patient_id,
                "PROBE_REMOVAL_SIMULATED",
                probe_start,
                probe_end,
                timestamps,
                "Pulse-oximeter-like signal loss; HR and SpO2 are missing rather than fabricated.",
                "hr_bpm;spo2_percent",
            )
        )

        dropout_start = int(count * 0.82)
        dropout_end = min(count - 1, dropout_start + max(1, int(count * 0.05)) - 1)
        labels.iloc[dropout_start : dropout_end + 1] = "PACKET_DROPOUT_SIMULATED"
        injected.iloc[dropout_start : dropout_end + 1] = True
        for vital in VITALS:
            final[vital].iloc[dropout_start : dropout_end + 1] = np.nan
            missing_reasons[vital].iloc[dropout_start : dropout_end + 1] = (
                "SIMULATED_PACKET_DROPOUT"
            )
        temperature[dropout_start : dropout_end + 1] = np.nan
        temperature_missing_reason.iloc[dropout_start : dropout_end + 1] = (
            "SIMULATED_PACKET_DROPOUT"
        )
        events.append(
            event_record(
                patient_id,
                "PACKET_DROPOUT_SIMULATED",
                dropout_start,
                dropout_end,
                timestamps,
                "All transmitted vital channels are missing during the simulated packet dropout.",
                "hr_bpm;rr_bpm;spo2_percent;temp_c_synthetic",
            )
        )

    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "patient_id": patient_id,
            "bidmc_recording": f"bidmc{RECORDINGS[patient_id]}",
            "source_filename": filename,
            "source_time_s": source_time,
            **{f"{vital}_source": original[vital] for vital in VITALS},
            **{f"{vital}_scenario_delta": deltas[vital] for vital in VITALS},
            "temp_c_synthetic": temperature,
            **{vital: final[vital] for vital in VITALS},
            **{f"{vital}_missing_reason": missing_reasons[vital] for vital in VITALS},
            "temp_c_missing_reason": temperature_missing_reason,
            "temperature_provenance": "SYNTHETIC_SIMULATION_ONLY",
            "event_label": labels,
            "is_injected": injected,
        },
        columns=OUTPUT_COLUMNS,
    )
    return frame, events


def main() -> None:
    selection = load_json(SYNTHEA_DIR / "selection.json")
    if set(selection.get("selections", {})) != set(SCENARIOS):
        raise ValueError("Synthea selection must contain exactly P001 through P005")
    PATIENTS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    profiles: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for patient_id, scenario_details in SCENARIOS.items():
        recording = RECORDINGS[patient_id]
        source, mapping, filename = read_source(recording)
        stream, events = make_patient_stream(patient_id, source, mapping, filename)
        patient_dir = PATIENTS_DIR / patient_id
        patient_dir.mkdir(parents=True, exist_ok=True)
        stream_path = patient_dir / "vital_stream.csv"
        events_path = patient_dir / "scenario_events.csv"
        stream.to_csv(stream_path, index=False)
        pd.DataFrame(events, columns=EVENT_COLUMNS).to_csv(events_path, index=False)

        synthea_selection = selection["selections"][patient_id]
        profile = {
            "patient_id": patient_id,
            "scenario": scenario_details["scenario"],
            "scenario_description": scenario_details["description"],
            "profile_origin": "Synthea-generated FHIR Patient resource",
            "source_linkage_policy": (
                "The Synthea profile and BIDMC recording are independent source records "
                "paired only for this demo; no identity or clinical relationship is asserted."
            ),
            "synthea": {
                "patient_resource_id": synthea_selection["patient_resource_id"],
                "synthea_patient_id": synthea_selection["synthea_patient_id"],
                "source_bundle": synthea_selection["source_bundle"],
                "selected_bundle": synthea_selection["selected_bundle"],
                "generated_candidate_filename": synthea_selection[
                    "generated_candidate_filename"
                ],
                "selected_bundle_sha256": synthea_selection["selected_bundle_sha256"],
                "release_tag": selection["release_tag"],
                "release_commit": selection["release_commit"],
                "jar_sha256": selection["jar_sha256"],
                "seed": selection["seed"],
                "candidate_population_size": selection["candidate_population_size"],
                "conditions": synthea_selection["conditions"],
                "medications": synthea_selection["medications"],
                "matched_condition": synthea_selection["matched_condition"],
                "demographics": synthea_selection["synthea_demographics"],
            },
            "bidmc_source": {
                "recording_id": f"bidmc{recording}",
                "numerics_filename": filename,
                "numerics_source_path": f"data/bidmc_original/{filename}",
                "source_url": (
                    "https://physionet.org/files/bidmc/1.0.0/bidmc_csv/" + filename
                ),
                "mapped_headers": mapping,
                "source_rows": len(source),
                "timestamp_policy": (
                    "Simulation timestamp is the synthetic 2026-01-01 UTC anchor plus "
                    "the source Time [s] offset; it is not an acquisition timestamp."
                ),
            },
            "temperature_channel": {
                "column": "temp_c_synthetic",
                "provenance": "SYNTHETIC_SIMULATION_ONLY",
                "source_data_available": False,
                "missingness_policy": (
                    "Missing during explicitly labelled simulated packet dropouts; "
                    "never forward-filled."
                ),
            },
            "source_value_policy": (
                "Original HR, RESP, and SpO2 values are retained in *_source columns. "
                "Scenario deltas and final values are separate; source gaps are not filled."
            ),
            "scenario_parameters": scenario_details.get("simulation_parameter"),
            "locally_defined_scenario_labels": scenario_details.get(
                "locally_defined_labels", []
            ),
            "scenario_events_file": f"data/patients/{patient_id}/scenario_events.csv",
            "vital_stream_file": f"data/patients/{patient_id}/vital_stream.csv",
            "synthetic_research_prototype_only": True,
            "not_clinically_validated": True,
        }
        (patient_dir / "patient_profile.json").write_text(
            json.dumps(profile, indent=2) + "\n", encoding="utf-8"
        )
        profiles.append(profile)
        manifest.append(
            {
                "patient_id": patient_id,
                "scenario": scenario_details["scenario"],
                "bidmc_recording": f"bidmc{recording}",
                "bidmc_numerics_filename": filename,
                "bidmc_numerics_source_path": f"data/bidmc_original/{filename}",
                "synthea_patient_id": synthea_selection["synthea_patient_id"],
                "synthea_selected_bundle": synthea_selection["selected_bundle"],
                "vital_stream_file": f"data/patients/{patient_id}/vital_stream.csv",
                "scenario_events_file": f"data/patients/{patient_id}/scenario_events.csv",
                "row_count": len(stream),
                "event_count": len(events),
            }
        )
        print(
            f"{patient_id}: {len(stream)} rows; BIDMC bidmc{recording}; "
            f"Synthea Patient/{synthea_selection['synthea_patient_id']}"
        )

    (PROFILE_DIR / "patients.json").write_text(
        json.dumps(profiles, indent=2) + "\n", encoding="utf-8"
    )
    pd.DataFrame(manifest).to_csv(DATA / "manifest.csv", index=False)
    print(f"Built exactly five patient datasets under {PATIENTS_DIR}")


if __name__ == "__main__":
    main()
