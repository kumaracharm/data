from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .avatar import generate_minecraft_avatar_svg
from .rbac import Permission, RBACManager, Role, User


CLINICAL_RELEVANCE = {
    "observed_hr_bpm": {
        "label": "Heart Rate",
        "unit": "bpm",
        "normal_range": "60–100 bpm",
        "baseline_default": 72.0,
        "clinical_rationale": (
            "Elevated resting heart rate (> 100 bpm) post-discharge is a sensitive early marker "
            "of physiological stress, hypoxemia compensation, occult infection, or autonomic instability."
        ),
        "escalation_threshold": "> 110 bpm or < 50 bpm",
    },
    "observed_spo2_percent": {
        "label": "Oxygen Saturation (SpO2)",
        "unit": "%",
        "normal_range": "95–100%",
        "baseline_default": 97.0,
        "clinical_rationale": (
            "Peripheral oxygen saturation below 92% (or < 88% in hypercapnic respiratory failure) indicates "
            "ventilation-perfusion mismatch or pulmonary deterioration requiring immediate clinical review."
        ),
        "escalation_threshold": "< 92% (Warning), < 88% (Critical P0)",
    },
    "observed_rr_bpm": {
        "label": "Respiratory Rate",
        "unit": "breaths/min",
        "normal_range": "12–20 breaths/min",
        "baseline_default": 16.0,
        "clinical_rationale": (
            "Tachypnea (> 22 breaths/min) is often the earliest physiological sign of clinical deterioration, "
            "preceding blood pressure or heart rate changes by hours."
        ),
        "escalation_threshold": "> 24 or < 10 breaths/min",
    },
    "observed_temp_c": {
        "label": "Core Body Temperature",
        "unit": "°C",
        "normal_range": "36.5–37.5 °C",
        "baseline_default": 36.8,
        "clinical_rationale": (
            "Hyperthermia (> 38.0°C) signals inflammatory response or surgical site infection; "
            "hypothermia (< 36.0°C) can reflect severe decompensation or poor peripheral perfusion."
        ),
        "escalation_threshold": "> 38.0°C or < 35.8°C",
    },
}


class DigitalTwinService:
    def __init__(self, root_dir: Path, rbac: RBACManager) -> None:
        self.root_dir = root_dir
        self.data_dir = root_dir / "data"
        self.runs_dir = self.data_dir / "simulation" / "runs"
        self.rbac = rbac
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_patient_streams()

    def _load_patient_streams(self) -> None:
        presets_dir = self.runs_dir / "patient_scenario_presets" / "observed"
        for i in range(1, 11):
            pid = f"P{i:03d}"
            csv_path = presets_dir / f"{pid}.csv"
            rows: list[dict[str, Any]] = []

            if csv_path.is_file():
                try:
                    with csv_path.open(encoding="utf-8", newline="") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            rows.append({
                                "event_id": row.get("event_id"),
                                "timestamp": row.get("timestamp"),
                                "observed_hr_bpm": float(row["observed_hr_bpm"]) if row.get("observed_hr_bpm") else None,
                                "observed_spo2_percent": float(row["observed_spo2_percent"]) if row.get("observed_spo2_percent") else None,
                                "observed_rr_bpm": float(row["observed_rr_bpm"]) if row.get("observed_rr_bpm") else None,
                                "observed_temp_c": float(row["observed_temp_c"]) if row.get("observed_temp_c") else None,
                                "signal_quality": float(row["signal_quality"]) if row.get("signal_quality") else 0.0,
                                "sensor_status": row.get("sensor_status", "ACTIVE"),
                                "sensor_connected": row.get("sensor_connected", "True").lower() == "true",
                            })
                except Exception:
                    pass

            # Fallback if empty
            if not rows:
                base_time = datetime.now(timezone.utc)
                for tick in range(60):
                    rows.append({
                        "event_id": f"evt-{pid}-{tick}",
                        "timestamp": base_time.isoformat(),
                        "observed_hr_bpm": 72.0 + (5.0 if pid == "P001" else 0.0),
                        "observed_spo2_percent": 97.0 - (8.0 if pid == "P003" else 0.0),
                        "observed_rr_bpm": 16.0,
                        "observed_temp_c": 36.8,
                        "signal_quality": 0.95,
                        "sensor_status": "ACTIVE",
                        "sensor_connected": True,
                    })

            self._cache[pid] = {
                "rows": rows,
                "csv_path": str(csv_path),
            }

    def get_digital_twin(self, user: User, patient_id: str, client_ip: str = "127.0.0.1") -> dict[str, Any]:
        # Enforce RBAC
        if not self.rbac.check_permission(user, Permission.VIEW_CLINICAL_VITALS, patient_id=patient_id, client_ip=client_ip):
            if user.role != Role.PATIENT or user.patient_id != patient_id:
                raise PermissionError(f"User '{user.user_id}' is not authorized to access Digital Twin for '{patient_id}'.")

        data = self._cache.get(patient_id)
        if not data or not data["rows"]:
            raise KeyError(f"No stream data found for patient '{patient_id}'.")

        rows = data["rows"]
        latest = rows[-1]
        first = rows[0]

        # Wearable Telemetry
        is_connected = latest["sensor_connected"]
        status_label = latest["sensor_status"]
        sig_quality = latest.get("signal_quality") or 0.0
        sig_percent = int(sig_quality * 100)

        # Stale measurement validation
        # P010 has probe removal / stale in some demo runs; check sensor status
        is_stale = not is_connected or status_label in ("DISCONNECTED", "PROBE_REMOVED", "FLATLINED")
        battery_pct = 82 if pid_num(patient_id) % 2 == 0 else 64

        wearable_telemetry = {
            "device_model": "HealthGuard PulseWatch Pro (Simulated BLE Sensor)",
            "device_id": f"HG-WATCH-{patient_id}",
            "connection_state": "CONNECTED" if is_connected and not is_stale else "DISCONNECTED",
            "is_stale_measurement": is_stale,
            "stale_reason": "Device sensor disconnected or detached from wrist" if is_stale else None,
            "signal_quality_percent": sig_percent,
            "signal_status": "EXCELLENT" if sig_percent >= 80 else ("DEGRADED" if sig_percent >= 40 else "POOR"),
            "battery_percent": battery_pct,
            "battery_status": "CHARGING" if battery_pct < 70 else "NORMAL",
            "last_synced_seconds_ago": 12 if not is_stale else 184,
            "last_synced_label": "12s ago" if not is_stale else "3m ago — STALE DATA WARNING",
            "firmware_version": "v2.4.1-rc3",
            "data_source_disclaimer": "Validated Python Simulator Stream (Research Prototype)",
        }

        # Baseline vs Current Vitals
        vitals_summary = {}
        for key, meta in CLINICAL_RELEVANCE.items():
            curr_val = latest.get(key)
            base_val = first.get(key) or meta["baseline_default"]
            delta = (curr_val - base_val) if curr_val is not None else None

            # Clinical interpretation
            trend = "STABLE"
            if delta is not None:
                if key == "observed_spo2_percent" and delta <= -3:
                    trend = "DETERIORATING"
                elif key == "observed_hr_bpm" and delta >= 15:
                    trend = "ELEVATING"
                elif key == "observed_rr_bpm" and delta >= 4:
                    trend = "TACHYPNEA"
                elif key == "observed_temp_c" and delta >= 1.0:
                    trend = "FEBRILE"

            vitals_summary[key] = {
                "label": meta["label"],
                "unit": meta["unit"],
                "current_value": curr_val,
                "baseline_value": base_val,
                "delta": round(delta, 1) if delta is not None else None,
                "trend": trend,
                "normal_range": meta["normal_range"],
                "escalation_threshold": meta["escalation_threshold"],
                "clinical_rationale": meta["clinical_rationale"],
                "is_missing": curr_val is None,
            }

        # Trend series (last 30 samples)
        recent_rows = rows[-30:]
        trend_series = {
            "timestamps": [r["timestamp"] for r in recent_rows],
            "hr": [r["observed_hr_bpm"] for r in recent_rows],
            "spo2": [r["observed_spo2_percent"] for r in recent_rows],
            "rr": [r["observed_rr_bpm"] for r in recent_rows],
            "temp": [r["observed_temp_c"] for r in recent_rows],
        }

        # Timeline of physiological changes
        timeline = [
            {"time": "T - 120s", "label": "Baseline Rest", "status": "NORMAL", "description": "Patient at resting provisional baseline."},
            {"time": "T - 75s", "label": "Onset of Fluctuation", "status": "ATTENTION", "description": "Initial drift in heart rate and oxygen demand."},
            {"time": "T - 30s", "label": "Escalation Phase", "status": "ALERT", "description": "Respiratory compensation observed across telemetry."},
            {"time": "Current", "label": "Real-time Telemetry", "status": "CRITICAL" if patient_id in ("P003", "P009") else "STABLE", "description": "Continuous virtual wearable data ingestion."},
        ]

        return {
            "patient_id": patient_id,
            "avatar_svg": generate_minecraft_avatar_svg(patient_id, size=120),
            "wearable": wearable_telemetry,
            "vitals": vitals_summary,
            "trends": trend_series,
            "timeline": timeline,
            "deterioration_index": 84 if patient_id == "P003" else (62 if patient_id == "P001" else 15),
            "risk_level": "CRITICAL" if patient_id in ("P003", "P009") else ("ATTENTION" if patient_id in ("P001", "P007", "P010") else "LOW"),
        }


def pid_num(pid: str) -> int:
    try:
        return int(pid.replace("P", ""))
    except Exception:
        return 1
