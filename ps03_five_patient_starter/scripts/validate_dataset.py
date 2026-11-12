from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import ijson


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PATIENT_IDS = [f"P{number:03d}" for number in range(1, 6)]
REQUIRED_STREAM_COLUMNS = {
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
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Validation:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.rows_per_patient: dict[str, int] = {}
        self.sources: dict[str, str] = {}
        self.synthea_mapping: dict[str, dict[str, Any]] = {}

    def check(self, name: str, passed: bool, details: Any = None) -> None:
        check = {"name": name, "passed": bool(passed)}
        if details is not None:
            check["details"] = details
        self.checks.append(check)
        if not passed:
            self.errors.append(name)

    def fail(self, name: str, details: str) -> None:
        self.check(name, False, details)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read {path}: {error}") from error


def check_patient(validation: Validation, patient_id: str) -> None:
    patient_dir = DATA / "patients" / patient_id
    expected_files = {"patient_profile.json", "vital_stream.csv", "scenario_events.csv"}
    existing_files = {path.name for path in patient_dir.iterdir()} if patient_dir.is_dir() else set()
    validation.check(
        f"{patient_id}: required patient files",
        expected_files.issubset(existing_files),
        {"expected": sorted(expected_files), "found": sorted(existing_files)},
    )
    if not expected_files.issubset(existing_files):
        return

    profile = load_json(patient_dir / "patient_profile.json")
    stream = pd.read_csv(patient_dir / "vital_stream.csv")
    events = pd.read_csv(patient_dir / "scenario_events.csv")
    missing_columns = sorted(REQUIRED_STREAM_COLUMNS - set(stream.columns))
    validation.check(
        f"{patient_id}: required stream columns",
        not missing_columns,
        {"missing_columns": missing_columns},
    )
    validation.check(
        f"{patient_id}: profile id and stream rows",
        profile.get("patient_id") == patient_id
        and len(stream) > 0
        and set(stream["patient_id"].dropna()) == {patient_id},
        {"row_count": len(stream), "profile_patient_id": profile.get("patient_id")},
    )
    validation.check(
        f"{patient_id}: independent Synthea and BIDMC records are not identity-linked",
        profile.get("source_linkage_policy")
        == (
            "The Synthea profile and BIDMC recording are independent source records "
            "paired only for this demo; no identity or clinical relationship is asserted."
        ),
    )
    validation.rows_per_patient[patient_id] = len(stream)

    recording = str(profile.get("bidmc_source", {}).get("recording_id", ""))
    validation.sources[patient_id] = recording
    validation.check(
        f"{patient_id}: temperature is explicitly synthetic",
        profile.get("temperature_channel", {}).get("provenance")
        == "SYNTHETIC_SIMULATION_ONLY"
        and profile.get("temperature_channel", {}).get("source_data_available") is False
        and set(stream.get("temperature_provenance", pd.Series(dtype=str)).dropna())
        == {"SYNTHETIC_SIMULATION_ONLY"},
        {"temperature_column": "temp_c_synthetic", "provenance": profile.get("temperature_channel")},
    )

    source_path = DATA / "bidmc_original" / profile["bidmc_source"]["numerics_filename"]
    source = pd.read_csv(source_path)
    mapping = profile["bidmc_source"]["mapped_headers"]
    source_value_checks: dict[str, bool] = {}
    for vital in ("hr_bpm", "rr_bpm", "spo2_percent"):
        expected = pd.to_numeric(source[mapping[vital]], errors="coerce").to_numpy(dtype=float)
        actual = pd.to_numeric(stream[f"{vital}_source"], errors="coerce").to_numpy(dtype=float)
        source_value_checks[vital] = (
            len(expected) == len(actual)
            and np.allclose(expected, actual, rtol=0.0, atol=0.0, equal_nan=True)
        )
    source_times = pd.to_numeric(source[mapping["time_s"]], errors="coerce").to_numpy(dtype=float)
    output_times = pd.to_numeric(stream["source_time_s"], errors="coerce").to_numpy(dtype=float)
    source_value_checks["time_s"] = (
        len(source_times) == len(output_times)
        and np.allclose(source_times, output_times, rtol=0.0, atol=0.0, equal_nan=True)
    )
    validation.check(
        f"{patient_id}: source values exactly preserved without filling",
        all(source_value_checks.values()),
        source_value_checks,
    )

    missing_reason_checks: dict[str, bool] = {}
    for vital in ("hr_bpm", "rr_bpm", "spo2_percent"):
        output = pd.to_numeric(stream[vital], errors="coerce")
        source_missing = pd.to_numeric(stream[f"{vital}_source"], errors="coerce").isna()
        reason = stream[f"{vital}_missing_reason"].astype("string").fillna("")
        missing_reason_checks[vital] = bool(
            (output[source_missing].isna()).all()
            and (reason[source_missing] == "SOURCE_READING_MISSING").all()
            and (output.isna() == reason.ne("")).all()
        )
    temp = pd.to_numeric(stream["temp_c_synthetic"], errors="coerce")
    temp_reason = stream["temp_c_missing_reason"].astype("string").fillna("")
    missing_reason_checks["temp_c_synthetic"] = bool(
        (temp.isna() == temp_reason.ne("")).all()
        and (temp_reason[ temp.isna()] == "SIMULATED_PACKET_DROPOUT").all()
    )
    validation.check(
        f"{patient_id}: missing values are explicitly explained",
        all(missing_reason_checks.values()),
        missing_reason_checks,
    )

    event_time_checks: list[bool] = []
    covered_rows = np.zeros(len(stream), dtype=bool)
    timestamps = stream["timestamp"].astype(str)
    for _, event in events.iterrows():
        start = int(event["start_index"])
        end = int(event["end_index"])
        in_bounds = 0 <= start <= end < len(stream)
        if not in_bounds:
            event_time_checks.append(False)
            continue
        covered_rows[start : end + 1] = True
        event_time_checks.append(
            timestamps.iloc[start] == str(event["start_timestamp"])
            and timestamps.iloc[end] == str(event["end_timestamp"])
            and bool(stream["is_injected"].iloc[start : end + 1].astype(bool).all())
        )
    injected_rows = stream["is_injected"].fillna(False).astype(bool).to_numpy()
    event_time_checks.append(bool(np.array_equal(injected_rows, covered_rows)))
    validation.check(
        f"{patient_id}: event timestamps match injected rows",
        all(event_time_checks),
        {
            "event_count": len(events),
            "events": events[["event_label", "start_timestamp", "end_timestamp"]].to_dict(
                orient="records"
            ),
            "injected_row_count": int(injected_rows.sum()),
        },
    )

    fhir_path = ROOT / profile["synthea"]["selected_bundle"]
    selection = load_json(DATA / "synthea_original" / "selection.json")
    selection_record = selection.get("selections", {}).get(patient_id, {})
    resource_ids: dict[str, set[str]] = {}
    patient_resource_ids: list[str] = []
    try:
        with fhir_path.open("rb") as source_bundle:
            for resource in ijson.items(source_bundle, "entry.item.resource"):
                if not isinstance(resource, dict):
                    continue
                resource_type = resource.get("resourceType", "")
                resource_ids.setdefault(resource_type, set()).add(str(resource.get("id", "")))
                if resource_type == "Patient":
                    patient_resource_ids.append(str(resource.get("id", "")))
    except (OSError, ijson.JSONError) as error:
        validation.fail(f"{patient_id}: Synthea bundle is traceable", str(error))
    else:
        condition_ids = {
            str(item.get("resource_id")) for item in selection_record.get("conditions", [])
        }
        medication_ids = {
            str(item.get("resource_id")) for item in selection_record.get("medications", [])
        }
        synthea_ok = (
            patient_resource_ids == [str(profile["synthea"]["patient_resource_id"])]
            and condition_ids.issubset(resource_ids.get("Condition", set()))
            and medication_ids.issubset(
                resource_ids.get("MedicationRequest", set())
                | resource_ids.get("MedicationStatement", set())
                | resource_ids.get("MedicationAdministration", set())
            )
            and bool(condition_ids)
            and bool(medication_ids)
            and selection_record.get("source_bundle") == profile["synthea"]["selected_bundle"]
            and sha256_file(fhir_path) == selection_record.get("selected_bundle_sha256")
        )
        validation.check(
            f"{patient_id}: Synthea provenance and resources are traceable",
            synthea_ok,
            {
                "patient_resource_id": profile["synthea"]["patient_resource_id"],
                "condition_resource_count": len(condition_ids),
                "medication_resource_count": len(medication_ids),
                "bundle": str(fhir_path.relative_to(ROOT)),
            },
        )
        validation.synthea_mapping[patient_id] = {
            "synthea_patient_id": profile["synthea"]["synthea_patient_id"],
            "selected_bundle": profile["synthea"]["selected_bundle"],
            "condition_resource_ids": sorted(condition_ids),
            "medication_resource_ids": sorted(medication_ids),
        }

    if patient_id == "P002":
        parameters = profile.get("scenario_parameters") or {}
        spo2 = pd.to_numeric(stream["spo2_percent"], errors="coerce")
        valid = (
            parameters.get("range_percent") == [88, 92]
            and parameters.get("parameter_is_simulation_only") is True
            and parameters.get("not_medical_advice") is True
            and bool(spo2.dropna().between(88, 92).all())
        )
        validation.check(
            "P002: SpO2 88-92% is simulation-only test configuration",
            valid,
            parameters,
        )


def main() -> int:
    validation = Validation()
    expected_patient_dirs = set(PATIENT_IDS)
    actual_patient_dirs = {
        path.name for path in (DATA / "patients").glob("P*") if path.is_dir()
    } if (DATA / "patients").is_dir() else set()
    validation.check(
        "exactly five patient directories",
        actual_patient_dirs == expected_patient_dirs,
        {"expected": sorted(expected_patient_dirs), "found": sorted(actual_patient_dirs)},
    )

    try:
        profiles = json.loads((DATA / "profiles" / "patients.json").read_text(encoding="utf-8"))
        validation.check(
            "exactly five consolidated patient profiles",
            len(profiles) == 5 and {item.get("patient_id") for item in profiles} == expected_patient_dirs,
            {"profile_count": len(profiles)},
        )
    except (OSError, json.JSONDecodeError, TypeError) as error:
        profiles = []
        validation.fail("consolidated profiles are readable", str(error))

    try:
        synthea_selection = load_json(DATA / "synthea_original" / "selection.json")
        jar_path = DATA / "synthea_original" / synthea_selection["jar_filename"]
        provenance_valid = (
            synthea_selection.get("generator") == "Synthea"
            and synthea_selection.get("jar_sha256") == sha256_file(jar_path)
            and set(synthea_selection.get("selections", {})) == expected_patient_dirs
            and len(
                {
                    item.get("synthea_patient_id")
                    for item in synthea_selection["selections"].values()
                }
            )
            == 5
        )
        validation.check(
            "Synthea release, JAR hash, and five unique selections are traceable",
            provenance_valid,
            {
                "release_tag": synthea_selection.get("release_tag"),
                "jar_sha256": synthea_selection.get("jar_sha256"),
                "selected_patient_count": len(synthea_selection.get("selections", {})),
            },
        )
    except (OSError, KeyError, ValueError, TypeError) as error:
        validation.fail("Synthea release provenance is readable", str(error))

    for patient_id in PATIENT_IDS:
        try:
            check_patient(validation, patient_id)
        except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError) as error:
            validation.fail(f"{patient_id}: patient dataset checks completed", str(error))

    validation.check(
        "exactly five distinct BIDMC recordings",
        len(validation.sources) == 5 and len(set(validation.sources.values())) == 5,
        validation.sources,
    )
    validation.check(
        "exactly five patient vital streams",
        sum(1 for patient_id in PATIENT_IDS if (DATA / "patients" / patient_id / "vital_stream.csv").is_file())
        == 5,
        validation.rows_per_patient,
    )
    validation.check(
        "manifest has exactly five patients",
        (DATA / "manifest.csv").is_file()
        and len(pd.read_csv(DATA / "manifest.csv")) == 5,
    )

    report = {
        "dataset": "PS-03 Five Patient Research Prototype",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_passed": not validation.errors,
        "patient_profile_count": len(profiles),
        "vital_stream_count": sum(
            1
            for patient_id in PATIENT_IDS
            if (DATA / "patients" / patient_id / "vital_stream.csv").is_file()
        ),
        "rows_per_patient": validation.rows_per_patient,
        "bidmc_recordings": validation.sources,
        "synthea_profile_mapping": validation.synthea_mapping,
        "checks": validation.checks,
        "errors": validation.errors,
        "scope_notice": (
            "Hackathon research prototype only; synthetic profiles and modifications are not "
            "clinically validated and are not diagnostic or medical advice."
        ),
    }
    report_path = ROOT / "dataset_validation_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["validation_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
