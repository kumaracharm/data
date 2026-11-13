from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .avatar import PATIENT_AVATAR_PRESETS, generate_minecraft_avatar_svg
from .rbac import Permission, RBACManager, Role, User


PATIENT_ASSIGNMENTS = {
    "P001": {
        "pheon": "Marcus Ward",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Arthur Chen, MD",
        "ward": "Cardiopulmonary Stepdown - Ward A",
        "room": "Room 101",
    },
    "P002": {
        "pheon": "Marcus Ward",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Emily Taylor, MD",
        "ward": "Post-Surgical Telemetry - Ward A",
        "room": "Room 102",
    },
    "P003": {
        "pheon": "Marcus Ward",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Arthur Chen, MD",
        "ward": "Congestive Heart Failure Unit - Ward A",
        "room": "Room 103",
    },
    "P004": {
        "pheon": "James Lee",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Robert Vance, MD",
        "ward": "Coronary Care Stepdown - Ward B",
        "room": "Room 201",
    },
    "P005": {
        "pheon": "James Lee",
        "nurse": "David Rodriguez, RN",
        "doctor": "Dr. Emily Taylor, MD",
        "ward": "Post-CABG Stepdown - Ward B",
        "room": "Room 202",
    },
    "P006": {
        "pheon": "James Lee",
        "nurse": "David Rodriguez, RN",
        "doctor": "Dr. Arthur Chen, MD",
        "ward": "Respiratory Care Unit - Ward B",
        "room": "Room 203",
    },
    "P007": {
        "pheon": "Marcus Ward",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Robert Vance, MD",
        "ward": "General Surgery Stepdown - Ward A",
        "room": "Room 104",
    },
    "P008": {
        "pheon": "Elena Rostova",
        "nurse": "David Rodriguez, RN",
        "doctor": "Dr. Robert Vance, MD",
        "ward": "Ambulatory Recovery - Ward C",
        "room": "Room 301",
    },
    "P009": {
        "pheon": "Elena Rostova",
        "nurse": "Sarah Jenkins, RN",
        "doctor": "Dr. Arthur Chen, MD",
        "ward": "Acute Cardiac Monitoring - Ward C",
        "room": "Room 302",
    },
    "P010": {
        "pheon": "Elena Rostova",
        "nurse": "David Rodriguez, RN",
        "doctor": "Dr. Arthur Chen, MD",
        "ward": "Post-Infectious Recovery - Ward C",
        "room": "Room 303",
    },
}


def _determine_age_group(age: int | None) -> str:
    if age is None:
        return "Adult"
    if age < 25:
        return f"{age} yrs • Young Adult"
    if age < 45:
        return f"{age} yrs • Adult"
    if age < 65:
        return f"{age} yrs • Middle-aged"
    return f"{age} yrs • Senior"


class PatientService:
    def __init__(self, root_dir: Path, rbac: RBACManager) -> None:
        self.root_dir = root_dir
        self.data_dir = root_dir / "data"
        self.profiles_dir = self.data_dir / "monitoring_profiles"
        self.fhir_dir = self.data_dir / "synthea_original" / "original_fhir"
        self.rbac = rbac
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_cohort()

    def _load_cohort(self) -> None:
        for i in range(1, 11):
            patient_id = f"P{i:03d}"
            profile_path = self.profiles_dir / f"{patient_id}.json"
            fhir_path = self.fhir_dir / f"{patient_id}.json"

            profile_data = {}
            if profile_path.is_file():
                try:
                    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Demographic lookup from presets
            preset = PATIENT_AVATAR_PRESETS.get(patient_id, {})
            name = preset.get("name", f"Patient {patient_id}")
            gender = preset.get("gender", "unspecified").capitalize()
            age = profile_data.get("age_at_encounter_end")
            age_group = _determine_age_group(age)

            # Clean condition labels (remove synthetic duplicates)
            raw_conditions = profile_data.get("historical_condition_labels", [])
            clean_conditions: list[str] = []
            for c in raw_conditions:
                # E.g. "Essential hypertension (disorder) Essential hypertension (disorder)" -> deduplicate
                parts = c.split(" (")
                if parts:
                    clean = parts[0].strip()
                    if clean and clean not in clean_conditions:
                        clean_conditions.append(clean)

            # Clean medication prescriptions
            raw_meds = profile_data.get("historical_medication_prescription_labels", [])
            clean_meds = [m.strip() for m in raw_meds if m and m.strip()]

            # Assignments
            assignment = PATIENT_ASSIGNMENTS.get(patient_id, {
                "pheon": "Assigned Ward Attendant",
                "nurse": "Assigned Nurse",
                "doctor": "Assigned Doctor",
                "ward": "Stepdown Ward",
                "room": "Room --",
            })

            # Check-in timestamp (deterministic recent time)
            checkin_time = "Today, 08:30 AM" if i % 2 == 1 else "Yesterday, 07:45 PM (Pending)"

            # Status default based on patient scenario
            status = "STABLE"
            if patient_id in ("P003", "P009"):
                status = "URGENT"
            elif patient_id in ("P001", "P007", "P010"):
                status = "ATTENTION"

            self._cache[patient_id] = {
                "patient_id": patient_id,
                "display_name": name,
                "gender": gender,
                "age": age,
                "age_group": age_group,
                "status": status,
                "avatar_svg": generate_minecraft_avatar_svg(patient_id),
                "primary_diagnosis": profile_data.get("recorded_encounter_reason", "Post-Discharge Follow-up"),
                "encounter_end": profile_data.get("encounter_end"),
                "discharge_disposition": profile_data.get("discharge_disposition") or "Home care with virtual monitoring",
                "conditions": clean_conditions[:8],
                "medications": clean_meds[:6],
                "assigned_pheon": assignment["pheon"],
                "assigned_nurse": assignment["nurse"],
                "assigned_doctor": assignment["doctor"],
                "ward": assignment["ward"],
                "room": assignment["room"],
                "last_checkin": checkin_time,
                "has_fhir": fhir_path.is_file(),
                "fhir_bundle_size_bytes": fhir_path.stat().st_size if fhir_path.is_file() else 0,
            }

    def get_patient(self, user: User, patient_id: str, client_ip: str = "127.0.0.1") -> dict[str, Any]:
        # Enforce RBAC permission
        if not self.rbac.check_permission(user, Permission.VIEW_ASSIGNED_PATIENTS, patient_id=patient_id, client_ip=client_ip):
            if user.role != Role.PATIENT or user.patient_id != patient_id:
                raise PermissionError(f"User '{user.user_id}' is not authorized to view patient '{patient_id}'.")

        patient = self._cache.get(patient_id)
        if not patient:
            raise KeyError(f"Patient '{patient_id}' not found.")

        # Data minimization based on role:
        # Patient sees only self-relevant info
        if user.role == Role.PATIENT:
            return {
                "patient_id": patient["patient_id"],
                "display_name": patient["display_name"],
                "age_group": patient["age_group"],
                "status": patient["status"],
                "avatar_svg": patient["avatar_svg"],
                "assigned_nurse": patient["assigned_nurse"],
                "assigned_doctor": patient["assigned_doctor"],
                "ward": patient["ward"],
                "last_checkin": patient["last_checkin"],
                "prescribed_medications": patient["medications"],
            }

        # PHEON sees only operational essentials (no full medical history)
        if user.role == Role.PHEON:
            return {
                "patient_id": patient["patient_id"],
                "display_name": patient["display_name"],
                "age_group": patient["age_group"],
                "status": patient["status"],
                "avatar_svg": patient["avatar_svg"],
                "assigned_nurse": patient["assigned_nurse"],
                "assigned_pheon": patient["assigned_pheon"],
                "ward": patient["ward"],
                "room": patient["room"],
                "last_checkin": patient["last_checkin"],
                "operational_guidance": "If SpO2 < 90% or patient reports breathing distress, escalate immediately to nurse.",
            }

        # Nurse, Doctor, Senior Doctor see complete clinical summary
        return dict(patient)

    def list_patients(self, user: User, client_ip: str = "127.0.0.1") -> list[dict[str, Any]]:
        # Patient role cannot list all patients
        if user.role == Role.PATIENT:
            if not user.patient_id or user.patient_id not in self._cache:
                return []
            return [self.get_patient(user, user.patient_id, client_ip=client_ip)]

        # Senior doctor can see all patients
        if user.role == Role.SENIOR_DOCTOR:
            allowed_ids = list(self._cache.keys())
        else:
            # PHEON, Nurse, Doctor see only assigned patients
            allowed_ids = [pid for pid in user.assigned_patients if pid in self._cache]

        results = []
        for pid in allowed_ids:
            try:
                results.append(self.get_patient(user, pid, client_ip=client_ip))
            except PermissionError:
                pass
        return results

    def export_fhir_bundle(self, user: User, patient_id: str, client_ip: str = "127.0.0.1") -> dict[str, Any]:
        # Enforce RBAC: Doctor & Senior Doctor ONLY
        if not self.rbac.check_permission(user, Permission.EXPORT_FHIR_RECORDS, patient_id=patient_id, client_ip=client_ip):
            self.rbac.audit.log(
                user_id=user.user_id,
                role=user.role.value,
                action="FHIR_EXPORT_ATTEMPT_DENIED",
                resource_type="FHIR_BUNDLE",
                resource_id=patient_id,
                status="FORBIDDEN",
                details={"reason": "Role lacks EXPORT_FHIR_RECORDS permission"},
                client_ip=client_ip,
            )
            raise PermissionError(f"Role '{user.role.value}' is not authorized to export raw FHIR records.")

        fhir_path = self.fhir_dir / f"{patient_id}.json"
        if not fhir_path.is_file():
            raise FileNotFoundError(f"FHIR record bundle for '{patient_id}' not found.")

        # Log successful authorized export in audit trail
        self.rbac.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="FHIR_EXPORT_AUTHORIZED",
            resource_type="FHIR_BUNDLE",
            resource_id=patient_id,
            status="SUCCESS",
            details={
                "exported_by": user.display_name,
                "role": user.role.value,
                "file_size_bytes": fhir_path.stat().st_size,
            },
            client_ip=client_ip,
        )

        return json.loads(fhir_path.read_text(encoding="utf-8"))
