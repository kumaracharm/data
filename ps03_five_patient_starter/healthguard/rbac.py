from __future__ import annotations

import enum
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class Role(str, enum.Enum):
    PATIENT = "patient"
    PHEON = "pheon"
    NURSE = "nurse"
    DOCTOR = "doctor"
    SENIOR_DOCTOR = "senior_doctor"


class Permission(str, enum.Enum):
    VIEW_OWN_PROFILE = "view_own_profile"
    SUBMIT_CHECKIN = "submit_checkin"
    VIEW_ASSIGNED_PATIENTS = "view_assigned_patients"
    VIEW_ALL_PATIENTS = "view_all_patients"
    VIEW_OPERATIONAL_STATUS = "view_operational_status"
    VIEW_CLINICAL_VITALS = "view_clinical_vitals"
    VIEW_DIAGNOSTIC_HISTORY = "view_diagnostic_history"
    VIEW_ALERTS = "view_alerts"
    ACKNOWLEDGE_ALERT = "acknowledge_alert"
    ESCALATE_ALERT = "escalate_alert"
    ADD_CLINICAL_NOTE = "add_clinical_note"
    CREATE_CARE_PLAN = "create_care_plan"
    EXPORT_FHIR_RECORDS = "export_fhir_records"
    REASSIGN_PATIENTS = "reassign_patients"
    OVERRIDE_WORKFLOW = "override_workflow"
    VIEW_OVERSIGHT_METRICS = "view_oversight_metrics"
    VIEW_AUDIT_LOGS = "view_audit_logs"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.PATIENT: {
        Permission.VIEW_OWN_PROFILE,
        Permission.SUBMIT_CHECKIN,
    },
    Role.PHEON: {
        Permission.VIEW_ASSIGNED_PATIENTS,
        Permission.VIEW_OPERATIONAL_STATUS,
        Permission.VIEW_ALERTS,
        Permission.ESCALATE_ALERT,
    },
    Role.NURSE: {
        Permission.VIEW_ASSIGNED_PATIENTS,
        Permission.VIEW_OPERATIONAL_STATUS,
        Permission.VIEW_CLINICAL_VITALS,
        Permission.VIEW_DIAGNOSTIC_HISTORY,
        Permission.VIEW_ALERTS,
        Permission.ACKNOWLEDGE_ALERT,
        Permission.ESCALATE_ALERT,
        Permission.ADD_CLINICAL_NOTE,
    },
    Role.DOCTOR: {
        Permission.VIEW_ASSIGNED_PATIENTS,
        Permission.VIEW_OPERATIONAL_STATUS,
        Permission.VIEW_CLINICAL_VITALS,
        Permission.VIEW_DIAGNOSTIC_HISTORY,
        Permission.VIEW_ALERTS,
        Permission.ACKNOWLEDGE_ALERT,
        Permission.ESCALATE_ALERT,
        Permission.ADD_CLINICAL_NOTE,
        Permission.CREATE_CARE_PLAN,
        Permission.EXPORT_FHIR_RECORDS,
    },
    Role.SENIOR_DOCTOR: {
        Permission.VIEW_ALL_PATIENTS,
        Permission.VIEW_ASSIGNED_PATIENTS,
        Permission.VIEW_OPERATIONAL_STATUS,
        Permission.VIEW_CLINICAL_VITALS,
        Permission.VIEW_DIAGNOSTIC_HISTORY,
        Permission.VIEW_ALERTS,
        Permission.ACKNOWLEDGE_ALERT,
        Permission.ESCALATE_ALERT,
        Permission.ADD_CLINICAL_NOTE,
        Permission.CREATE_CARE_PLAN,
        Permission.EXPORT_FHIR_RECORDS,
        Permission.REASSIGN_PATIENTS,
        Permission.OVERRIDE_WORKFLOW,
        Permission.VIEW_OVERSIGHT_METRICS,
        Permission.VIEW_AUDIT_LOGS,
    },
}


@dataclass
class User:
    user_id: str
    username: str
    display_name: str
    role: Role
    patient_id: str | None = None  # Populated only for Role.PATIENT
    assigned_patients: list[str] = field(default_factory=list)
    title: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class Session:
    token: str
    user_id: str
    role: Role
    patient_id: str | None
    created_at: float
    expires_at: float
    client_ip: str = "127.0.0.1"


@dataclass
class AuditRecord:
    timestamp: str
    event_id: str
    user_id: str
    role: str
    action: str
    resource_type: str
    resource_id: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    client_ip: str = "127.0.0.1"


class AuditLogger:
    def __init__(self, log_path: Path | None = None, max_in_memory: int = 500) -> None:
        self.log_path = log_path
        self.max_in_memory = max_in_memory
        self._entries: list[AuditRecord] = []
        if self.log_path and self.log_path.is_file():
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        try:
            raw = json.loads(self.log_path.read_text(encoding="utf-8"))
            for item in raw[-self.max_in_memory :]:
                self._entries.append(AuditRecord(**item))
        except Exception:
            pass

    def log(
        self,
        user_id: str,
        role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        status: str = "SUCCESS",
        details: dict[str, Any] | None = None,
        client_ip: str = "127.0.0.1",
    ) -> AuditRecord:
        record = AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_id=f"AUD-{int(time.time()*1000)}-{secrets.token_hex(4)}",
            user_id=user_id,
            role=role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            details=details or {},
            client_ip=client_ip,
        )
        self._entries.append(record)
        if len(self._entries) > self.max_in_memory:
            self._entries.pop(0)

        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                data = [asdict(e) for e in self._entries[-100:]]
                self.log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass
        return record

    def get_entries(
        self, limit: int = 50, role_filter: str | None = None
    ) -> list[dict[str, Any]]:
        entries = self._entries
        if role_filter:
            entries = [e for e in entries if e.role == role_filter]
        return [asdict(e) for e in reversed(entries[-limit:])]


class RBACManager:
    SESSION_DURATION_SECONDS = 3600 * 8  # 8 hours

    def __init__(self, audit_path: Path | None = None) -> None:
        self.audit = AuditLogger(audit_path)
        self._users: dict[str, User] = {}
        self._sessions: dict[str, Session] = {}
        self._setup_default_users()

    def _setup_default_users(self) -> None:
        # Default users for all five roles
        all_patients = [f"P{i:03d}" for i in range(1, 11)]

        # 1. Patients
        self._users["patient_p001"] = User(
            user_id="patient_p001",
            username="patient_p001",
            display_name="Clora Christiansen",
            role=Role.PATIENT,
            patient_id="P001",
            assigned_patients=["P001"],
            title="Patient (Post-Discharge)",
            email="clora.c@healthguard.local",
            phone="+1-555-0101",
        )
        self._users["patient_p003"] = User(
            user_id="patient_p003",
            username="patient_p003",
            display_name="Jarrod O'Keefe",
            role=Role.PATIENT,
            patient_id="P003",
            assigned_patients=["P003"],
            title="Patient (Post-Discharge)",
            email="jarrod.o@healthguard.local",
            phone="+1-555-0103",
        )
        for i in (2, 4, 5, 6, 7, 8, 9, 10):
            pid = f"P{i:03d}"
            uid = f"patient_{pid.lower()}"
            self._users[uid] = User(
                user_id=uid,
                username=uid,
                display_name=f"Patient {pid}",
                role=Role.PATIENT,
                patient_id=pid,
                assigned_patients=[pid],
                title="Patient",
            )

        # 2. Role B: PHEON / Ward Attendant (Marcus Ward)
        self._users["pheon_marcus"] = User(
            user_id="pheon_marcus",
            username="pheon_marcus",
            display_name="Marcus Ward",
            role=Role.PHEON,
            assigned_patients=["P001", "P002", "P003", "P007"],
            title="PHEON / Ward Attendant",
            email="marcus.ward@hospital.org",
            phone="+1-555-0201",
        )

        # 3. Role C: Nurse (Sarah Jenkins RN)
        self._users["nurse_sarah"] = User(
            user_id="nurse_sarah",
            username="nurse_sarah",
            display_name="Sarah Jenkins, RN",
            role=Role.NURSE,
            assigned_patients=["P001", "P002", "P003", "P004", "P007", "P009"],
            title="Clinical Monitoring Nurse",
            email="sarah.jenkins@hospital.org",
            phone="+1-555-0301",
        )

        # 4. Role D: Doctor (Dr. Arthur Chen MD)
        self._users["dr_chen"] = User(
            user_id="dr_chen",
            username="dr_chen",
            display_name="Dr. Arthur Chen, MD",
            role=Role.DOCTOR,
            assigned_patients=["P001", "P003", "P006", "P009", "P010"],
            title="Cardiopulmonary Attending Physician",
            email="arthur.chen@hospital.org",
            phone="+1-555-0401",
        )

        # 5. Role E: Senior Doctor / Clinical Administrator (Dr. Maya Patel MD)
        self._users["dr_patel"] = User(
            user_id="dr_patel",
            username="dr_patel",
            display_name="Dr. Maya Patel, MD",
            role=Role.SENIOR_DOCTOR,
            assigned_patients=all_patients,
            title="Chief Medical Officer / Clinical Director",
            email="maya.patel@hospital.org",
            phone="+1-555-0501",
        )

    def authenticate(
        self, username: str, client_ip: str = "127.0.0.1"
    ) -> tuple[Session, User]:
        user = self._users.get(username)
        if not user:
            self.audit.log(
                user_id=username,
                role="UNKNOWN",
                action="LOGIN_FAILED",
                resource_type="AUTH",
                resource_id=username,
                status="FAILED",
                details={"reason": "User not found"},
                client_ip=client_ip,
            )
            raise PermissionError(f"User '{username}' not found.")

        token = f"hg_sess_{secrets.token_urlsafe(32)}"
        now = time.time()
        session = Session(
            token=token,
            user_id=user.user_id,
            role=user.role,
            patient_id=user.patient_id,
            created_at=now,
            expires_at=now + self.SESSION_DURATION_SECONDS,
            client_ip=client_ip,
        )
        self._sessions[token] = session

        self.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="LOGIN_SUCCESS",
            resource_type="AUTH",
            resource_id=user.user_id,
            status="SUCCESS",
            details={"display_name": user.display_name, "role": user.role.value},
            client_ip=client_ip,
        )
        return session, user

    def validate_session(self, token: str) -> tuple[Session, User]:
        if not token:
            raise PermissionError("Missing authentication token.")

        session = self._sessions.get(token)
        if not session:
            raise PermissionError("Invalid or expired session token.")

        if time.time() > session.expires_at:
            del self._sessions[token]
            raise PermissionError("Session has expired.")

        user = self._users.get(session.user_id)
        if not user:
            raise PermissionError("User record not found for active session.")

        return session, user

    def logout(self, token: str) -> None:
        if token in self._sessions:
            sess = self._sessions.pop(token)
            self.audit.log(
                user_id=sess.user_id,
                role=sess.role.value,
                action="LOGOUT",
                resource_type="AUTH",
                resource_id=sess.user_id,
                status="SUCCESS",
                client_ip=sess.client_ip,
            )

    def check_permission(
        self,
        user: User,
        permission: Permission,
        patient_id: str | None = None,
        audit_on_deny: bool = True,
        client_ip: str = "127.0.0.1",
    ) -> bool:
        # 1. Deny-by-default role permission check
        allowed_perms = ROLE_PERMISSIONS.get(user.role, set())
        if permission not in allowed_perms:
            if audit_on_deny:
                self.audit.log(
                    user_id=user.user_id,
                    role=user.role.value,
                    action="PERMISSION_DENIED",
                    resource_type="PERMISSION",
                    resource_id=permission.value,
                    status="FORBIDDEN",
                    details={
                        "reason": f"Role '{user.role.value}' does not have permission '{permission.value}'"
                    },
                    client_ip=client_ip,
                )
            return False

        # 2. Scope check: If a specific patient_id is requested
        if patient_id:
            # Patient can only access their own patient_id
            if user.role == Role.PATIENT:
                if user.patient_id != patient_id:
                    if audit_on_deny:
                        self.audit.log(
                            user_id=user.user_id,
                            role=user.role.value,
                            action="PATIENT_SCOPE_DENIED",
                            resource_type="PATIENT",
                            resource_id=patient_id,
                            status="FORBIDDEN",
                            details={
                                "reason": f"Patient '{user.patient_id}' attempted to access '{patient_id}'"
                            },
                            client_ip=client_ip,
                        )
                    return False

            # Senior Doctor has global cohort scope
            elif user.role == Role.SENIOR_DOCTOR:
                return True

            # PHEON, Nurse, Doctor must be assigned to the patient
            else:
                if patient_id not in user.assigned_patients:
                    if audit_on_deny:
                        self.audit.log(
                            user_id=user.user_id,
                            role=user.role.value,
                            action="ASSIGNMENT_SCOPE_DENIED",
                            resource_type="PATIENT",
                            resource_id=patient_id,
                            status="FORBIDDEN",
                            details={
                                "reason": f"User '{user.user_id}' ({user.role.value}) not assigned to '{patient_id}'"
                            },
                            client_ip=client_ip,
                        )
                    return False

        return True

    def reassign_patient(
        self,
        senior_user: User,
        target_patient_id: str,
        assignee_user_id: str,
        reason: str,
        client_ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        if not self.check_permission(
            senior_user, Permission.REASSIGN_PATIENTS, client_ip=client_ip
        ):
            raise PermissionError("Only Senior Doctors can reassign patients.")

        target_user = self._users.get(assignee_user_id)
        if not target_user:
            raise ValueError(f"Target staff '{assignee_user_id}' does not exist.")

        if target_patient_id not in target_user.assigned_patients:
            target_user.assigned_patients.append(target_patient_id)

        self.audit.log(
            user_id=senior_user.user_id,
            role=senior_user.role.value,
            action="PATIENT_REASSIGNMENT",
            resource_type="PATIENT",
            resource_id=target_patient_id,
            status="SUCCESS",
            details={
                "assigned_to": assignee_user_id,
                "assignee_name": target_user.display_name,
                "reason": reason,
            },
            client_ip=client_ip,
        )
        return {
            "patient_id": target_patient_id,
            "assigned_to": assignee_user_id,
            "assignee_name": target_user.display_name,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def override_workflow(
        self,
        senior_user: User,
        target_patient_id: str,
        override_type: str,
        reason: str,
        client_ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        if not self.check_permission(
            senior_user, Permission.OVERRIDE_WORKFLOW, client_ip=client_ip
        ):
            raise PermissionError("Only Senior Doctors can override workflows.")

        if not reason or len(reason.strip()) < 5:
            raise ValueError("An explicit audit reason is required for workflow override.")

        record = self.audit.log(
            user_id=senior_user.user_id,
            role=senior_user.role.value,
            action="WORKFLOW_OVERRIDE",
            resource_type="PATIENT",
            resource_id=target_patient_id,
            status="SUCCESS",
            details={"override_type": override_type, "reason": reason},
            client_ip=client_ip,
        )
        return {
            "override_id": record.event_id,
            "patient_id": target_patient_id,
            "override_type": override_type,
            "reason": reason,
            "authorized_by": senior_user.display_name,
            "timestamp": record.timestamp,
        }

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def list_users(self) -> list[dict[str, Any]]:
        return [
            {
                "user_id": u.user_id,
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role.value,
                "title": u.title,
                "patient_id": u.patient_id,
                "assigned_patients": u.assigned_patients,
                "email": u.email,
                "phone": u.phone,
            }
            for u in self._users.values()
        ]
