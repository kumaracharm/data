from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA


@dataclass(frozen=True)
class PatientProfile:
    patient_id: str
    fhir_patient_id: str
    age_at_encounter_end: int | None
    encounter_id: str
    encounter_end: datetime
    encounter_reason: str
    discharge_disposition: dict[str, Any] | None
    historical_conditions: tuple[str, ...]
    historical_prescription_labels: tuple[str, ...]
    historical_vital_loinc_codes: tuple[str, ...]
    historical_observations: tuple[dict[str, Any], ...]
    proposed_scenario: str
    spo2_scale: int
    oxygen_target: Any
    baseline_status: str
    fhir_bundle_path: Path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read cohort file {path}: {error}") from error


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _observation_time(resource: dict[str, Any]) -> datetime | None:
    value = resource.get("effectiveDateTime") or resource.get("issued")
    if value:
        return _parse_datetime(value)
    period = resource.get("effectivePeriod") or {}
    value = period.get("end") or period.get("start")
    return _parse_datetime(value) if value else None


def _observation_value(resource: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if "valueQuantity" in resource:
        values.append(resource["valueQuantity"])
    for component in resource.get("component", []):
        if "valueQuantity" in component:
            values.append(
                {
                    "component_code": component.get("code"),
                    **component["valueQuantity"],
                }
            )
    return values


def _historical_observations(
    bundle: dict[str, Any], patient_id: str, encounter_end: datetime
) -> tuple[dict[str, Any], ...]:
    historical: list[dict[str, Any]] = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Observation":
            continue
        reference = resource.get("subject", {}).get("reference", "")
        if reference and reference not in {
            f"Patient/{patient_id}",
            f"urn:uuid:{patient_id}",
        }:
            continue
        timestamp = _observation_time(resource)
        if timestamp is None or timestamp > encounter_end:
            continue
        historical.append(
            {
                "observation_id": resource.get("id"),
                "timestamp": timestamp.isoformat(),
                "codes": resource.get("code", {}).get("coding", []),
                "display": resource.get("code", {}).get("text"),
                "values": _observation_value(resource),
                "historical_only": True,
            }
        )
    return tuple(historical)


def load_patients(
    data_dir: Path | None = None, expected_count: int = 10
) -> list[PatientProfile]:
    root = data_dir or DATA
    cohort_dir = root / "synthea_original"
    registry = _read_json(cohort_dir / "patient_registry.json")
    if not isinstance(registry, list) or len(registry) != expected_count:
        raise ValueError(
            f"Expected exactly {expected_count} verified cohort records in patient_registry.json"
        )
    expected_ids = {f"P{number:03d}" for number in range(1, expected_count + 1)}
    registry_ids = {record.get("patient_id") for record in registry}
    if registry_ids != expected_ids:
        raise ValueError(
            f"Expected cohort IDs {sorted(expected_ids)}, found {sorted(registry_ids)}"
        )
    profiles: list[PatientProfile] = []
    seen: set[str] = set()
    seen_fhir_ids: set[str] = set()
    for record in registry:
        patient_id = record["patient_id"]
        if patient_id in seen:
            raise ValueError(f"Duplicate registry patient ID: {patient_id}")
        seen.add(patient_id)
        profile_path = root / "monitoring_profiles" / f"{patient_id}.json"
        profile = _read_json(profile_path)
        if profile.get("patient_id") != patient_id:
            raise ValueError(f"{patient_id}: profile patient ID does not match registry")
        if profile.get("fhir_patient_id") != record.get("fhir_patient_id"):
            raise ValueError(f"{patient_id}: profile and registry FHIR IDs do not match")
        fhir_patient_id = str(profile["fhir_patient_id"])
        if fhir_patient_id in seen_fhir_ids:
            raise ValueError(f"Duplicate FHIR Patient ID in cohort: {fhir_patient_id}")
        seen_fhir_ids.add(fhir_patient_id)
        bundle_path = cohort_dir / "original_fhir" / f"{patient_id}.json"
        bundle = _read_json(bundle_path)
        patient_resources = [
            item.get("resource", {})
            for item in bundle.get("entry", [])
            if item.get("resource", {}).get("resourceType") == "Patient"
        ]
        if (
            len(patient_resources) != 1
            or patient_resources[0].get("id") != profile["fhir_patient_id"]
        ):
            raise ValueError(f"{patient_id}: FHIR bundle Patient resource does not match profile")
        encounter_end = _parse_datetime(profile["encounter_end"])
        historical = _historical_observations(
            bundle, profile["fhir_patient_id"], encounter_end
        )
        monitoring = profile.get("monitoring_configuration") or {}
        if monitoring.get("initial_vitals") is not None:
            raise ValueError(
                f"{patient_id}: the supplied revised cohort says initial vitals are pending; "
                "do not replace them with historical measurements."
            )
        profiles.append(
            PatientProfile(
                patient_id=patient_id,
                fhir_patient_id=fhir_patient_id,
                age_at_encounter_end=profile.get("age_at_encounter_end"),
                encounter_id=profile["encounter_id"],
                encounter_end=encounter_end,
                encounter_reason=profile.get("recorded_encounter_reason", ""),
                discharge_disposition=profile.get("discharge_disposition"),
                historical_conditions=tuple(profile.get("historical_condition_labels", [])),
                historical_prescription_labels=tuple(
                    item
                    for item in profile.get("historical_medication_prescription_labels", [])
                    if item
                ),
                historical_vital_loinc_codes=tuple(
                    profile.get("historical_vital_loinc_codes", [])
                ),
                historical_observations=historical,
                proposed_scenario=profile.get("proposed_simulator_scenario", ""),
                spo2_scale=int(monitoring.get("spo2_scale", 1)),
                oxygen_target=monitoring.get("oxygen_target"),
                baseline_status=str(monitoring.get("baseline_status", "unknown")),
                fhir_bundle_path=bundle_path,
            )
        )
    if len(seen) != expected_count:
        raise ValueError(f"Expected {expected_count} unique patient IDs, found {len(seen)}")
    return profiles
