from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .rbac import Permission, RBACManager, Role, User


@dataclass
class VoiceCommandResult:
    intent: str
    target: str | None
    requires_confirmation: bool
    confirmation_message: str | None
    spoken_response: str
    action_type: str  # NAVIGATE, FILTER, ACKNOWLEDGE, ESCALATE, UNKNOWN
    parameters: dict[str, Any]
    authorized: bool


class VoiceService:
    def __init__(self, rbac: RBACManager) -> None:
        self.rbac = rbac

    def parse_command(self, raw_text: str, user: User) -> VoiceCommandResult:
        text = raw_text.strip().lower()

        # 1. "Show urgent patients" / "filter urgent"
        if re.search(r"\b(show|view|find)\s+(urgent|critical|high[- ]risk)\b", text) or "urgent patients" in text:
            if user.role == Role.PATIENT:
                return VoiceCommandResult(
                    intent="FILTER_URGENT",
                    target=None,
                    requires_confirmation=False,
                    confirmation_message=None,
                    spoken_response="Voice command not available for patient role.",
                    action_type="FORBIDDEN",
                    parameters={},
                    authorized=False,
                )
            return VoiceCommandResult(
                intent="FILTER_URGENT",
                target="urgent",
                requires_confirmation=False,
                confirmation_message=None,
                spoken_response="Filtering to urgent and deteriorating patients.",
                action_type="FILTER",
                parameters={"status": "URGENT"},
                authorized=True,
            )

        # 2. "Open patient P003" / "navigate to P001"
        match_pid = re.search(r"\b(open|show|go to|view)\s+patient\s+(p0\d\d|p\d\d\d)\b", text) or re.search(r"\b(p0\d\d|p\d\d\d)\b", text)
        if match_pid:
            pid = match_pid.group(match_pid.lastindex).upper()
            # RBAC check
            if not self.rbac.check_permission(user, Permission.VIEW_ASSIGNED_PATIENTS, patient_id=pid, audit_on_deny=False):
                if user.role != Role.PATIENT or user.patient_id != pid:
                    return VoiceCommandResult(
                        intent="NAVIGATE_PATIENT",
                        target=pid,
                        requires_confirmation=False,
                        confirmation_message=None,
                        spoken_response=f"Access denied. You are not authorized to access patient {pid}.",
                        action_type="FORBIDDEN",
                        parameters={"patient_id": pid},
                        authorized=False,
                    )
            return VoiceCommandResult(
                intent="NAVIGATE_PATIENT",
                target=pid,
                requires_confirmation=False,
                confirmation_message=None,
                spoken_response=f"Opening patient workspace for {pid}.",
                action_type="NAVIGATE",
                parameters={"patient_id": pid, "view": "digital_twin"},
                authorized=True,
            )

        # 3. "Show today's missed check-ins" / "missed checkins"
        if "missed" in text and ("check" in text or "patient" in text):
            if user.role == Role.PATIENT:
                return VoiceCommandResult(
                    intent="SHOW_MISSED",
                    target=None,
                    requires_confirmation=False,
                    confirmation_message=None,
                    spoken_response="Access restricted to clinical staff.",
                    action_type="FORBIDDEN",
                    parameters={},
                    authorized=False,
                )
            return VoiceCommandResult(
                intent="SHOW_MISSED",
                target="missed_checkins",
                requires_confirmation=False,
                confirmation_message=None,
                spoken_response="Displaying patients with overdue or missed daily check-ins.",
                action_type="FILTER",
                parameters={"filter": "missed_checkins"},
                authorized=True,
            )

        # 4. "Show patients assigned to me" / "my patients"
        if re.search(r"\b(assigned to me|my patients|my list)\b", text):
            return VoiceCommandResult(
                intent="SHOW_MY_PATIENTS",
                target="assigned",
                requires_confirmation=False,
                confirmation_message=None,
                spoken_response=f"Filtering to patients assigned to {user.display_name}.",
                action_type="FILTER",
                parameters={"assigned_to": user.user_id},
                authorized=True,
            )

        # 5. "Acknowledge this alert" / "acknowledge alert"
        if "acknowledge" in text and "alert" in text:
            # Sensitive clinical action -> MUST require confirmation
            if not self.rbac.check_permission(user, Permission.ACKNOWLEDGE_ALERT, audit_on_deny=False):
                return VoiceCommandResult(
                    intent="ACKNOWLEDGE_ALERT",
                    target=None,
                    requires_confirmation=False,
                    confirmation_message=None,
                    spoken_response="Your role does not have permission to acknowledge clinical alerts.",
                    action_type="FORBIDDEN",
                    parameters={},
                    authorized=False,
                )
            return VoiceCommandResult(
                intent="ACKNOWLEDGE_ALERT",
                target="active_alert",
                requires_confirmation=True,
                confirmation_message="Clinical confirmation required: Do you confirm acknowledgement of this active alert?",
                spoken_response="Please confirm on-screen to finalize alert acknowledgement.",
                action_type="ACKNOWLEDGE",
                parameters={},
                authorized=True,
            )

        # 6. "Open patient history" / "medical records"
        if "history" in text or "medical records" in text or "fhir" in text:
            if not self.rbac.check_permission(user, Permission.VIEW_DIAGNOSTIC_HISTORY, audit_on_deny=False):
                return VoiceCommandResult(
                    intent="OPEN_HISTORY",
                    target=None,
                    requires_confirmation=False,
                    confirmation_message=None,
                    spoken_response="Diagnostic history is not accessible for this role.",
                    action_type="FORBIDDEN",
                    parameters={},
                    authorized=False,
                )
            return VoiceCommandResult(
                intent="OPEN_HISTORY",
                target="history",
                requires_confirmation=False,
                confirmation_message=None,
                spoken_response="Navigating to patient medical history and records.",
                action_type="NAVIGATE",
                parameters={"tab": "history"},
                authorized=True,
            )

        # Fallback
        return VoiceCommandResult(
            intent="UNKNOWN",
            target=None,
            requires_confirmation=False,
            confirmation_message=None,
            spoken_response=f"Unrecognized voice command: '{raw_text}'. Try saying 'Show urgent patients' or 'Open patient P003'.",
            action_type="UNKNOWN",
            parameters={"raw_text": raw_text},
            authorized=False,
        )
