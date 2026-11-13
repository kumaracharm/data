from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .rbac import RBACManager, User


CHECKIN_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q1_overall_feeling",
        "question": "How are you feeling today overall?",
        "helper": "Choose the option that best reflects how you feel this morning.",
        "type": "single_choice",
        "options": [
            {"value": "great", "label": "Great", "icon": "😊", "score": 0},
            {"value": "good", "label": "Good", "icon": "🙂", "score": 0},
            {"value": "fair", "label": "Fair / A bit tired", "icon": "😐", "score": 1},
            {"value": "unwell", "label": "Unwell", "icon": "😟", "score": 2},
            {"value": "very_sick", "label": "Very Sick / In distress", "icon": "😣", "score": 4},
        ],
    },
    {
        "id": "q2_breathing",
        "question": "Are you experiencing any breathing difficulty or shortness of breath?",
        "helper": "Notice how you breathe while resting or talking.",
        "type": "single_choice",
        "options": [
            {"value": "none", "label": "No breathing difficulty", "icon": "🫁", "score": 0},
            {"value": "mild", "label": "Mild shortness of breath with activity", "icon": "😮‍💨", "score": 2},
            {"value": "severe", "label": "Severe breathing difficulty even while resting", "icon": "⚠️", "score": 5},
        ],
        "triggers_adaptive": ["mild", "severe"],
    },
    {
        "id": "q3_fatigue",
        "question": "Are you feeling unusually tired or weak today?",
        "helper": "Compared to your typical energy level after leaving the hospital.",
        "type": "single_choice",
        "options": [
            {"value": "none", "label": "Normal energy levels", "icon": "⚡", "score": 0},
            {"value": "mild", "label": "Mild tiredness, but able to move around", "icon": "🥱", "score": 1},
            {"value": "severe", "label": "Extreme exhaustion, unable to do basic tasks", "icon": "🛌", "score": 3},
        ],
    },
    {
        "id": "q4_fever_discomfort",
        "question": "Do you have a fever, chills, or unusual body discomfort?",
        "helper": "Your wearable also tracks skin temperature trends.",
        "type": "single_choice",
        "options": [
            {"value": "none", "label": "No fever or chills", "icon": "🌡️", "score": 0},
            {"value": "mild", "label": "Mild feverish feeling or slight chills", "icon": "🤒", "score": 2},
            {"value": "high", "label": "High fever or severe shivering chills", "icon": "🔥", "score": 4},
        ],
    },
    {
        "id": "q5_medication",
        "question": "Have you taken your prescribed medications today?",
        "helper": "Adherence helps your care team evaluate recovery safely.",
        "type": "single_choice",
        "options": [
            {"value": "yes_all", "label": "Yes, taken all prescribed doses", "icon": "💊", "score": 0},
            {"value": "partial", "label": "Partially taken (missed some)", "icon": "⏳", "score": 2},
            {"value": "not_yet", "label": "Not yet, will take soon", "icon": "🕒", "score": 1},
            {"value": "skipped_side_effects", "label": "Skipped due to side effects or nausea", "icon": "❌", "score": 3},
        ],
    },
    {
        "id": "q6_new_symptoms",
        "question": "Are you experiencing any new or worsening symptoms?",
        "helper": "Select all that apply, or select None.",
        "type": "multi_choice",
        "options": [
            {"value": "none", "label": "None of these", "score": 0},
            {"value": "cough", "label": "Persistent or worsening cough", "score": 2},
            {"value": "chest_pain", "label": "Chest tightness or chest pain", "score": 5},
            {"value": "dizziness", "label": "Dizziness or feeling lightheaded", "score": 3},
            {"value": "swelling", "label": "Swelling in ankles, legs, or feet", "score": 2},
        ],
        "triggers_adaptive_if_contains": ["cough", "chest_pain", "dizziness"],
    },
]

ADAPTIVE_FOLLOWUP_QUESTIONS: dict[str, dict[str, Any]] = {
    "onset": {
        "id": "adaptive_onset",
        "question": "When did this symptom start?",
        "helper": "Timing helps your clinical team understand symptom progression.",
        "type": "single_choice",
        "options": [
            {"value": "last_hour", "label": "Within the last hour", "score": 3},
            {"value": "today", "label": "Earlier today", "score": 2},
            {"value": "past_2_days", "label": "Over the past 1–2 days", "score": 1},
        ],
    },
    "progression": {
        "id": "adaptive_progression",
        "question": "Is the symptom getting progressively worse?",
        "type": "single_choice",
        "options": [
            {"value": "rapidly_worse", "label": "Yes, getting noticeably worse quickly", "score": 4},
            {"value": "slowly_worse", "label": "Slightly worse over time", "score": 2},
            {"value": "steady", "label": "About the same", "score": 1},
            {"value": "improving", "label": "Improving", "score": 0},
        ],
    },
    "activity_impact": {
        "id": "adaptive_activity_impact",
        "question": "Is this symptom preventing you from your normal daily activities?",
        "type": "single_choice",
        "options": [
            {"value": "severe_impact", "label": "Yes, cannot walk across room or speak full sentences", "score": 5},
            {"value": "moderate_impact", "label": "Moderately difficult, have to sit frequently", "score": 2},
            {"value": "minimal_impact", "label": "Minor effect, still able to manage chores", "score": 1},
        ],
    },
}


@dataclass
class CheckinSubmission:
    submission_id: str
    patient_id: str
    timestamp: str
    answers: dict[str, Any]
    voice_note_transcript: str | None
    total_risk_score: int
    clinical_risk_tier: str  # LOW, MODERATE, HIGH, CRITICAL
    alert_triggered: bool
    assigned_nurse: str
    assigned_doctor: str


class CheckinService:
    def __init__(self, data_file: Path | None = None, rbac: RBACManager | None = None) -> None:
        self.data_file = data_file
        self.rbac = rbac
        self._history: dict[str, list[CheckinSubmission]] = {}
        self._reminder_configs: dict[str, dict[str, Any]] = {}
        self._calendar_cache: dict[str, list[dict[str, Any]]] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        # Prepopulate realistic 14-day calendar history for demo patients
        now = datetime.now(timezone.utc)
        for i in range(1, 11):
            pid = f"P{i:03d}"
            # Default reminder preferences
            self._reminder_configs[pid] = {
                "patient_id": pid,
                "preferred_time": "09:00",
                "reminders_enabled": True,
                "repeat_delay_minutes": 60,
                "escalate_after_minutes": 180,
                "channels": ["in_app", "wearable_vibrate", "sms"],
            }

            # Generate past 14 days calendar status
            days: list[dict[str, Any]] = []
            for d in range(13, -1, -1):
                date_obj = now - timedelta(days=d)
                date_str = date_obj.strftime("%Y-%m-%d")
                weekday = date_obj.strftime("%a")

                if d == 0:
                    # Today
                    if pid in ("P001", "P005", "P007"):
                        status = "COMPLETED"
                        time_str = "08:15 AM"
                    elif pid in ("P003", "P009"):
                        status = "FOLLOW_UP_REQUIRED"
                        time_str = "07:30 AM (High symptoms)"
                    else:
                        status = "PENDING"
                        time_str = None
                else:
                    # Historical days
                    if (d + i) % 5 == 0:
                        status = "MISSED"
                        time_str = None
                    elif (d + i) % 7 == 0:
                        status = "FOLLOW_UP_REQUIRED"
                        time_str = "09:12 AM (Reported fatigue)"
                    else:
                        status = "COMPLETED"
                        time_str = "08:45 AM"

                days.append({
                    "date": date_str,
                    "day_number": date_obj.day,
                    "weekday": weekday,
                    "status": status,  # COMPLETED, MISSED, FOLLOW_UP_REQUIRED, PENDING
                    "timestamp": time_str,
                })
            self._calendar_cache[pid] = days

    def get_questions_schema(self) -> dict[str, Any]:
        return {
            "standard_questions": CHECKIN_QUESTIONS,
            "adaptive_followups": ADAPTIVE_FOLLOWUP_QUESTIONS,
        }

    def get_patient_checkin_status(self, patient_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        calendar = self._calendar_cache.get(patient_id, [])
        today_entry = next((day for day in calendar if day["date"] == now.strftime("%Y-%m-%d")), None)

        is_completed = today_entry is not None and today_entry["status"] in ("COMPLETED", "FOLLOW_UP_REQUIRED")
        history = self._history.get(patient_id, [])

        return {
            "patient_id": patient_id,
            "today_date": now.strftime("%A, %B %d, %Y"),
            "is_completed_today": is_completed,
            "status": today_entry["status"] if today_entry else "PENDING",
            "last_completed_timestamp": today_entry["timestamp"] if is_completed else (history[-1].timestamp if history else "Yesterday, 08:30 AM"),
            "estimated_completion_minutes": 2,
            "next_reminder": "Today at 02:00 PM" if not is_completed else "Tomorrow at 09:00 AM",
            "reminders_config": self._reminder_configs.get(patient_id, {}),
        }

    def get_patient_calendar(self, patient_id: str) -> list[dict[str, Any]]:
        return self._calendar_cache.get(patient_id, [])

    def submit_checkin(
        self,
        patient_id: str,
        answers: dict[str, Any],
        voice_note: str | None = None,
        user: User | None = None,
        client_ip: str = "127.0.0.1",
    ) -> CheckinSubmission:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Score answers
        score = 0
        q_map = {q["id"]: q for q in CHECKIN_QUESTIONS}

        for q_id, ans in answers.items():
            if q_id in q_map:
                q_def = q_map[q_id]
                if q_def["type"] == "single_choice":
                    match = next((opt for opt in q_def["options"] if opt["value"] == ans), None)
                    if match:
                        score += match.get("score", 0)
                elif q_def["type"] == "multi_choice" and isinstance(ans, list):
                    for val in ans:
                        match = next((opt for opt in q_def["options"] if opt["value"] == val), None)
                        if match:
                            score += match.get("score", 0)
            elif q_id.startswith("adaptive_"):
                suffix = q_id.replace("adaptive_", "")
                if suffix in ADAPTIVE_FOLLOWUP_QUESTIONS:
                    match = next((opt for opt in ADAPTIVE_FOLLOWUP_QUESTIONS[suffix]["options"] if opt["value"] == ans), None)
                    if match:
                        score += match.get("score", 0)

        # Determine clinical tier
        if score >= 8:
            tier = "CRITICAL"
            alert_triggered = True
        elif score >= 5:
            tier = "HIGH"
            alert_triggered = True
        elif score >= 2:
            tier = "MODERATE"
            alert_triggered = False
        else:
            tier = "LOW"
            alert_triggered = False

        submission = CheckinSubmission(
            submission_id=f"CHK-{int(now.timestamp())}-{patient_id}",
            patient_id=patient_id,
            timestamp=now_iso,
            answers=answers,
            voice_note_transcript=voice_note,
            total_risk_score=score,
            clinical_risk_tier=tier,
            alert_triggered=alert_triggered,
            assigned_nurse="Sarah Jenkins, RN",
            assigned_doctor="Dr. Arthur Chen, MD",
        )

        if patient_id not in self._history:
            self._history[patient_id] = []
        self._history[patient_id].append(submission)

        # Update calendar for today
        today_str = now.strftime("%Y-%m-%d")
        cal = self._calendar_cache.get(patient_id, [])
        for day in cal:
            if day["date"] == today_str:
                day["status"] = "FOLLOW_UP_REQUIRED" if alert_triggered else "COMPLETED"
                day["timestamp"] = now.strftime("%I:%M %p")
                break

        # Audit log
        if self.rbac:
            self.rbac.audit.log(
                user_id=user.user_id if user else f"patient_{patient_id.lower()}",
                role=user.role.value if user else "patient",
                action="DAILY_CHECKIN_SUBMITTED",
                resource_type="PATIENT_CHECKIN",
                resource_id=submission.submission_id,
                status="SUCCESS",
                details={
                    "patient_id": patient_id,
                    "risk_score": score,
                    "risk_tier": tier,
                    "alert_triggered": alert_triggered,
                    "has_voice_note": bool(voice_note),
                },
                client_ip=client_ip,
            )

        return submission

    def get_patient_submissions(self, patient_id: str) -> list[dict[str, Any]]:
        submissions = self._history.get(patient_id, [])
        return [asdict(s) for s in reversed(submissions)]

    def list_missed_checkins(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        missed: list[dict[str, Any]] = []

        for pid, days in self._calendar_cache.items():
            today_day = next((d for d in days if d["date"] == today_str), None)
            if today_day and today_day["status"] == "PENDING":
                missed.append({
                    "patient_id": pid,
                    "preferred_time": self._reminder_configs.get(pid, {}).get("preferred_time", "09:00"),
                    "overdue_hours": 3,
                    "assigned_pheon": "Marcus Ward" if pid in ("P001", "P002", "P003", "P007") else "James Lee",
                    "assigned_nurse": "Sarah Jenkins, RN",
                })
        return missed
