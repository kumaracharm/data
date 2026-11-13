from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rbac import Permission, RBACManager, Role, User


class AlertPriority(str, enum.Enum):
    P0 = "P0"  # Immediate critical escalation
    P1 = "P1"  # Urgent clinical review
    P2 = "P2"  # Attention required
    P3 = "P3"  # Routine monitoring


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


@dataclass
class SmartwatchNotification:
    notification_id: str
    patient_id: str
    priority: AlertPriority
    header: str
    body: str
    lock_screen_text: str  # Privacy-preserving for locked screen
    measurement: str
    timestamp: str
    recommended_action: str
    deep_link: str
    target_role: str
    target_user_name: str


@dataclass
class Alert:
    alert_id: str
    patient_id: str
    patient_name: str
    priority: AlertPriority
    status: AlertStatus
    title: str
    trigger_reason: str
    vital_name: str
    measured_value: float | None
    unit: str
    threshold: str
    created_at: str
    created_timestamp: float
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None
    acknowledgement_notes: str | None = None
    escalation_tier: str = "PHEON"  # PHEON -> NURSE -> DOCTOR -> SENIOR_DOCTOR
    is_parallel_escalation: bool = False
    notified_roles: list[str] = field(default_factory=list)
    smartwatch_payload: dict[str, Any] | None = None
    channels_dispatched: list[str] = field(default_factory=list)


class AlertService:
    def __init__(self, rbac: RBACManager) -> None:
        self.rbac = rbac
        self._alerts: dict[str, Alert] = {}
        self._notification_history: list[dict[str, Any]] = []
        self._init_mock_alerts()

    def _init_mock_alerts(self) -> None:
        now = time.time()
        # Initial clinical alerts for demo patients P003 (P0 critical) and P001 (P1 urgent)
        p003_alert = self.create_alert(
            patient_id="P003",
            patient_name="Jarrod O'Keefe",
            priority=AlertPriority.P0,
            vital_name="SpO2",
            measured_value=88.0,
            unit="%",
            threshold="< 90%",
            trigger_reason="SpO2 below configured threshold (88% with persistent tachycardia 112 bpm)",
            recommended_action="Immediate bedside respiratory assessment and oxygen titration.",
        )
        p001_alert = self.create_alert(
            patient_id="P001",
            patient_name="Clora Christiansen",
            priority=AlertPriority.P1,
            vital_name="Heart Rate",
            measured_value=114.0,
            unit="bpm",
            threshold="> 110 bpm",
            trigger_reason="Heart rate exceeding post-discharge baseline for > 15 minutes",
            recommended_action="Review 120s vital trend and check today's daily medication intake.",
        )
        p010_alert = self.create_alert(
            patient_id="P010",
            patient_name="Asha Raynor",
            priority=AlertPriority.P2,
            vital_name="Sensor Signal",
            measured_value=35.0,
            unit="%",
            threshold="< 50%",
            trigger_reason="Smartwatch signal degradation / potential loose wristband",
            recommended_action="Prompt patient or attendant to verify wristband sensor contact.",
        )

    def create_alert(
        self,
        patient_id: str,
        patient_name: str,
        priority: AlertPriority,
        vital_name: str,
        measured_value: float | None,
        unit: str,
        threshold: str,
        trigger_reason: str,
        recommended_action: str,
    ) -> Alert:
        # Check duplicate suppression: If active alert for same patient and vital exists, avoid duplicate storm
        for existing in self._alerts.values():
            if (
                existing.patient_id == patient_id
                and existing.vital_name == vital_name
                and existing.status == AlertStatus.ACTIVE
            ):
                return existing

        now = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()
        alert_id = f"ALT-{int(now * 1000)}-{patient_id}"

        # Alert Routing:
        # P0: Parallel Escalation to Nurse, Doctor, and Senior Doctor concurrently
        if priority == AlertPriority.P0:
            is_parallel = True
            escalation_tier = "SENIOR_DOCTOR"  # Instantly available to all tiers
            notified_roles = ["nurse", "doctor", "senior_doctor", "pheon"]
        elif priority == AlertPriority.P1:
            is_parallel = False
            escalation_tier = "DOCTOR"
            notified_roles = ["nurse", "doctor", "pheon"]
        elif priority == AlertPriority.P2:
            is_parallel = False
            escalation_tier = "NURSE"
            notified_roles = ["pheon", "nurse"]
        else:  # P3
            is_parallel = False
            escalation_tier = "PHEON"
            notified_roles = ["pheon"]

        # Format Smartwatch Notification
        val_str = f"{measured_value:.1f}{unit}" if measured_value is not None else "N/A"
        sw_notification = SmartwatchNotification(
            notification_id=f"SWN-{alert_id}",
            patient_id=patient_id,
            priority=priority,
            header=f"[{priority.value} {priority.name}] Patient {patient_id}",
            body=f"{vital_name}: {val_str} ({threshold})\n{trigger_reason}\nAction: {recommended_action}",
            lock_screen_text=f"HealthGuard Alert: Patient {patient_id} requires immediate attention.",
            measurement=val_str,
            timestamp="Just now",
            recommended_action=recommended_action,
            deep_link=f"/app/digital-twin/{patient_id}",
            target_role="nurse",
            target_user_name="Sarah Jenkins, RN",
        )

        channels = ["in_app_banner", "smartwatch_feed", "simulated_web_push"]
        if priority in (AlertPriority.P0, AlertPriority.P1):
            channels.extend(["sms_relay_adapter", "hospital_telemetry_pager"])

        alert = Alert(
            alert_id=alert_id,
            patient_id=patient_id,
            patient_name=patient_name,
            priority=priority,
            status=AlertStatus.ACTIVE,
            title=f"[{priority.value}] {vital_name} Alert — {patient_name} ({patient_id})",
            trigger_reason=trigger_reason,
            vital_name=vital_name,
            measured_value=measured_value,
            unit=unit,
            threshold=threshold,
            created_at=now_iso,
            created_timestamp=now,
            escalation_tier=escalation_tier,
            is_parallel_escalation=is_parallel,
            notified_roles=notified_roles,
            smartwatch_payload=asdict(sw_notification),
            channels_dispatched=channels,
        )

        self._alerts[alert_id] = alert

        # Log dispatch
        self.rbac.audit.log(
            user_id="alert_engine",
            role="SYSTEM",
            action="ALERT_TRIGGERED",
            resource_type="ALERT",
            resource_id=alert_id,
            status="SUCCESS",
            details={
                "patient_id": patient_id,
                "priority": priority.value,
                "vital": vital_name,
                "value": val_str,
                "is_parallel": is_parallel,
                "channels": channels,
            },
        )
        return alert

    def acknowledge_alert(
        self,
        user: User,
        alert_id: str,
        notes: str | None = None,
        client_ip: str = "127.0.0.1",
    ) -> Alert:
        alert = self._alerts.get(alert_id)
        if not alert:
            raise KeyError(f"Alert '{alert_id}' not found.")

        # Check permissions
        if not self.rbac.check_permission(
            user, Permission.ACKNOWLEDGE_ALERT, patient_id=alert.patient_id, client_ip=client_ip
        ):
            raise PermissionError(f"User '{user.user_id}' cannot acknowledge alerts.")

        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
        alert.acknowledged_by = f"{user.display_name} ({user.role.value})"
        alert.acknowledgement_notes = notes or "Reviewed by clinician"

        self.rbac.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="ALERT_ACKNOWLEDGED",
            resource_type="ALERT",
            resource_id=alert_id,
            status="SUCCESS",
            details={"patient_id": alert.patient_id, "notes": notes},
            client_ip=client_ip,
        )
        return alert

    def escalate_alert(
        self,
        user: User,
        alert_id: str,
        escalate_to_role: str,
        reason: str,
        client_ip: str = "127.0.0.1",
    ) -> Alert:
        alert = self._alerts.get(alert_id)
        if not alert:
            raise KeyError(f"Alert '{alert_id}' not found.")

        if not self.rbac.check_permission(
            user, Permission.ESCALATE_ALERT, patient_id=alert.patient_id, client_ip=client_ip
        ):
            raise PermissionError(f"User '{user.user_id}' cannot escalate alerts.")

        alert.status = AlertStatus.ESCALATED
        alert.escalation_tier = escalate_to_role.upper()
        if escalate_to_role not in alert.notified_roles:
            alert.notified_roles.append(escalate_to_role)

        self.rbac.audit.log(
            user_id=user.user_id,
            role=user.role.value,
            action="ALERT_ESCALATED",
            resource_type="ALERT",
            resource_id=alert_id,
            status="SUCCESS",
            details={
                "patient_id": alert.patient_id,
                "escalated_to": escalate_to_role,
                "reason": reason,
            },
            client_ip=client_ip,
        )
        return alert

    def list_alerts(self, user: User, client_ip: str = "127.0.0.1") -> list[dict[str, Any]]:
        # Patient cannot see internal clinical alert list
        if user.role == Role.PATIENT:
            return []

        # Senior Doctor sees all alerts
        if user.role == Role.SENIOR_DOCTOR:
            allowed = list(self._alerts.values())
        else:
            # Filter by role notification & patient assignment
            allowed = [
                a
                for a in self._alerts.values()
                if (user.role.value in a.notified_roles or user.role == Role.DOCTOR)
                and a.patient_id in user.assigned_patients
            ]

        # Sort: Active first, then by priority P0 > P1 > P2 > P3, then latest
        priority_rank = {AlertPriority.P0: 0, AlertPriority.P1: 1, AlertPriority.P2: 2, AlertPriority.P3: 3}
        status_rank = {AlertStatus.ACTIVE: 0, AlertStatus.ESCALATED: 1, AlertStatus.ACKNOWLEDGED: 2, AlertStatus.RESOLVED: 3}

        sorted_alerts = sorted(
            allowed,
            key=lambda a: (status_rank.get(a.status, 9), priority_rank.get(a.priority, 9), -a.created_timestamp),
        )
        return [asdict(a) for a in sorted_alerts]

    def get_smartwatch_feed(self, user: User, client_ip: str = "127.0.0.1") -> list[dict[str, Any]]:
        # Feeds the Smartwatch alert view for authenticated staff
        if user.role == Role.PATIENT:
            return []
        alerts = self.list_alerts(user, client_ip=client_ip)
        return [a["smartwatch_payload"] for a in alerts if a.get("smartwatch_payload")]
