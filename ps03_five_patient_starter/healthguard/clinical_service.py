from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .rbac import Permission, RBACManager, Role, User


@dataclass
class ClinicalNote:
    note_id: str
    patient_id: str
    author_id: str
    author_name: str
    author_role: str
    note_type: str  # NURSE_OBSERVATION, PHYSICIAN_ASSESSMENT, CARE_PLAN, ESCALATION_REASON
    content: str
    timestamp: str


@dataclass
class CarePlan:
    plan_id: str
    patient_id: str
    created_by: str
    author_role: str
    target_spo2: str
    target_hr: str
    medication_orders: list[str]
    checkin_frequency: str  # DAILY, TWICE_DAILY, HOURLY_TELEMETRY
    followup_date: str
    instructions: str
    timestamp: str


class ClinicalService:
    def __init__(self, rbac: RBACManager) -> None:
        self.rbac = rbac
        self._notes: dict[str, list[ClinicalNote]] = {}
        self._plans: dict[str, CarePlan] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Initial clinical notes for P003 and P001
        self._notes["P003"] = [
            ClinicalNote(
                note_id="NOTE-P003-01",
                patient_id="P003",
                author_id="nurse_sarah",
                author_name="Sarah Jenkins, RN",
                author_role="nurse",
                note_type="NURSE_OBSERVATION",
                content="Patient reported mild nocturnal orthopnea during morning follow-up. Wearable SpO2 trending 88-90%. Escalate to Dr. Chen.",
                timestamp=now_iso,
            ),
            ClinicalNote(
                note_id="NOTE-P003-02",
                patient_id="P003",
                author_id="dr_chen",
                author_name="Dr. Arthur Chen, MD",
                author_role="doctor",
                note_type="PHYSICIAN_ASSESSMENT",
                content="Clinical Decision Support indicates compensated heart failure decompensation. Prescribed furosemide titration and ordered supplementary pulse oximetry calibration.",
                timestamp=now_iso,
            ),
        ]
        self._plans["P003"] = CarePlan(
            plan_id="PLAN-P003",
            patient_id="P003",
            created_by="Dr. Arthur Chen, MD",
            author_role="doctor",
            target_spo2=">= 93%",
            target_hr="65–85 bpm",
            medication_orders=["Furosemide 40mg PO daily", "Carvedilol 6.25mg BID"],
            checkin_frequency="TWICE_DAILY",
            followup_date="In 48 hours (Virtual Tele-visit)",
            instructions="Maintain strict fluid restriction < 1.5L/day. Monitor daily weights. Report any sudden weight gain > 2 lbs.",
            timestamp=now_iso,
        )

        self._notes["P001"] = [
            ClinicalNote(
                note_id="NOTE-P001-01",
                patient_id="P001",
                author_id="nurse_sarah",
                author_name="Sarah Jenkins, RN",
                author_role="nurse",
                note_type="NURSE_OBSERVATION",
                content="Patient completed morning check-in on time. Vital signs stable with mild transient tachycardia (108 bpm) resolving after rest.",
                timestamp=now_iso,
            )
        ]

    def add_note(
        self,
        user: User,
        patient_id: str,
        note_type: str,
        content: str,
        client_ip: str = "127.0.0.1",
    ) -> ClinicalNote:
        if not self.rbac.check_permission(
            user, Permission.ADD_CLINICAL_NOTE, patient_id=patient_id, client_ip=client_ip
        ):
            raise PermissionError(f"User '{user.user_id}' cannot add clinical notes.")

        now_iso = datetime.now(timezone.utc).isoformat()
        note = ClinicalNote(
            note_id=f"NOTE-{patient_id}-{int(time.time()*1000)}",
            patient_id=patient_id,
            author_id=user.user_id,
            author_name=user.display_name,
            author_role=user.role.value,
            note_type=note_type,
            content=content,
            timestamp=now_iso,
        )

        if patient_id not in self._notes:
            self._notes[patient_id] = []
        self._notes[patient_id].append(note)

        self.rbac.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="CLINICAL_NOTE_ADDED",
            resource_type="PATIENT_NOTE",
            resource_id=note.note_id,
            status="SUCCESS",
            details={"patient_id": patient_id, "note_type": note_type},
            client_ip=client_ip,
        )
        return note

    def create_care_plan(
        self,
        user: User,
        patient_id: str,
        plan_data: dict[str, Any],
        client_ip: str = "127.0.0.1",
    ) -> CarePlan:
        if not self.rbac.check_permission(
            user, Permission.CREATE_CARE_PLAN, patient_id=patient_id, client_ip=client_ip
        ):
            raise PermissionError(f"Role '{user.role.value}' cannot create care plans.")

        now_iso = datetime.now(timezone.utc).isoformat()
        plan = CarePlan(
            plan_id=f"PLAN-{patient_id}-{int(time.time()*1000)}",
            patient_id=patient_id,
            created_by=user.display_name,
            author_role=user.role.value,
            target_spo2=plan_data.get("target_spo2", ">= 94%"),
            target_hr=plan_data.get("target_hr", "60–90 bpm"),
            medication_orders=plan_data.get("medication_orders", []),
            checkin_frequency=plan_data.get("checkin_frequency", "DAILY"),
            followup_date=plan_data.get("followup_date", "In 7 days"),
            instructions=plan_data.get("instructions", "Continue virtual monitoring"),
            timestamp=now_iso,
        )
        self._plans[patient_id] = plan

        self.rbac.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="CARE_PLAN_CREATED",
            resource_type="CARE_PLAN",
            resource_id=plan.plan_id,
            status="SUCCESS",
            details={"patient_id": patient_id},
            client_ip=client_ip,
        )
        return plan

    def get_notes(self, user: User, patient_id: str, client_ip: str = "127.0.0.1") -> list[dict[str, Any]]:
        # Patient and PHEON cannot access doctor/clinical assessments
        if user.role in (Role.PATIENT, Role.PHEON):
            return []

        if not self.rbac.check_permission(
            user, Permission.VIEW_DIAGNOSTIC_HISTORY, patient_id=patient_id, client_ip=client_ip
        ):
            return []

        notes = self._notes.get(patient_id, [])
        return [asdict(n) for n in reversed(notes)]

    def get_care_plan(self, user: User, patient_id: str, client_ip: str = "127.0.0.1") -> dict[str, Any] | None:
        if user.role == Role.PATIENT and user.patient_id != patient_id:
            return None

        plan = self._plans.get(patient_id)
        return asdict(plan) if plan else None

    def get_oversight_metrics(self, user: User, client_ip: str = "127.0.0.1") -> dict[str, Any]:
        # Senior Doctor oversight only
        if not self.rbac.check_permission(
            user, Permission.VIEW_OVERSIGHT_METRICS, client_ip=client_ip
        ):
            raise PermissionError("Oversight metrics are restricted to Senior Doctor / Clinical Administrator.")

        return {
            "total_cohort_patients": 10,
            "status_summary": {
                "critical_urgent": 2,  # P003, P009
                "attention_required": 3,  # P001, P007, P010
                "stable": 5,  # P002, P004, P005, P006, P008
            },
            "unresolved_escalations": [
                {
                    "patient_id": "P003",
                    "patient_name": "Jarrod O'Keefe",
                    "priority": "P0",
                    "assigned_nurse": "Sarah Jenkins, RN",
                    "assigned_doctor": "Dr. Arthur Chen, MD",
                    "escalation_time": "14 minutes ago",
                    "trigger": "SpO2 < 88% with compensatory tachycardia",
                },
                {
                    "patient_id": "P009",
                    "patient_name": "Jarrett Mraz",
                    "priority": "P0",
                    "assigned_nurse": "Sarah Jenkins, RN",
                    "assigned_doctor": "Dr. Arthur Chen, MD",
                    "escalation_time": "26 minutes ago",
                    "trigger": "Sudden deterioration post-NSTEMI",
                },
            ],
            "staff_performance": {
                "average_nurse_acknowledgement_minutes": 3.4,
                "average_doctor_review_minutes": 7.8,
                "compliance_rate_percent": 94.2,
                "target_response_minutes": 5.0,
            },
            "workload_distribution": [
                {"role": "PHEON", "name": "Marcus Ward", "patients_count": 4, "active_alerts": 2},
                {"role": "PHEON", "name": "James Lee", "patients_count": 3, "active_alerts": 0},
                {"role": "PHEON", "name": "Elena Rostova", "patients_count": 3, "active_alerts": 1},
                {"role": "NURSE", "name": "Sarah Jenkins, RN", "patients_count": 6, "active_alerts": 3},
                {"role": "NURSE", "name": "David Rodriguez, RN", "patients_count": 4, "active_alerts": 0},
                {"role": "DOCTOR", "name": "Dr. Arthur Chen, MD", "patients_count": 5, "active_alerts": 2},
                {"role": "DOCTOR", "name": "Dr. Emily Taylor, MD", "patients_count": 2, "active_alerts": 0},
                {"role": "DOCTOR", "name": "Dr. Robert Vance, MD", "patients_count": 3, "active_alerts": 0},
            ],
            "adherence_metrics": {
                "daily_checkin_compliance_percent": 88.0,
                "missed_checkins_today": 3,
                "completed_checkins_today": 7,
            },
        }
