from __future__ import annotations

import argparse
import http.cookies
import json
import socketserver
import sys
import urllib.parse
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .alert_service import AlertPriority, AlertService
from .checkin_service import CheckinService
from .clinical_service import ClinicalService
from .digital_twin_service import DigitalTwinService
from .patient_service import PatientService
from .rbac import Permission, RBACManager, Role, User
from .ui import get_html_app
from .voice_service import VoiceService


class HealthGuardAPIHandler(BaseHTTPRequestHandler):
    # Server context injected by factory
    rbac: RBACManager
    patient_service: PatientService
    checkin_service: CheckinService
    alert_service: AlertService
    digital_twin_service: DigitalTwinService
    clinical_service: ClinicalService
    voice_service: VoiceService

    def log_message(self, format: str, *args: Any) -> None:
        # Minimal clean log output
        sys.stderr.write(f"[HealthGuard] {self.address_string()} - {format % args}\n")

    def _send_json(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html_text: str, status: int = 200) -> None:
        payload = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message, "status": status}, status=status)

    def _get_auth_token(self) -> str | None:
        # Check Authorization header: Bearer <token>
        auth_hdr = self.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            return auth_hdr.split("Bearer ", 1)[1].strip()

        # Check Cookie: hg_session=<token>
        cookie_hdr = self.headers.get("Cookie")
        if cookie_hdr:
            cookies = http.cookies.SimpleCookie()
            cookies.load(cookie_hdr)
            if "hg_session" in cookies:
                return cookies["hg_session"].value
        return None

    def _authenticate_request(self) -> tuple[Any, User]:
        token = self._get_auth_token()
        if not token:
            # Fallback to default user nurse_sarah for browser convenience if no token yet
            sess, user = self.rbac.authenticate("nurse_sarah", client_ip=self.client_address[0])
            return sess, user
        try:
            return self.rbac.validate_session(token)
        except PermissionError:
            # Expired or invalid -> authenticate as nurse_sarah
            return self.rbac.authenticate("nurse_sarah", client_ip=self.client_address[0])

    def _read_body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)
        except Exception as e:
            raise ValueError(f"Invalid JSON request body: {e}") from e

    # ----------------------------------------------------
    # GET ROUTER
    # ----------------------------------------------------
    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)
        client_ip = self.client_address[0]

        # 1. UI Root & Static Routes
        if path in ("/", "/app", "/index.html", "/patient_viewer.html"):
            _, default_user = self.rbac.authenticate("nurse_sarah", client_ip=client_ip)
            initial_data = json.dumps({"default_user": asdict(default_user)})
            self._send_html(get_html_app(initial_data))
            return

        # 2. Authentication: /api/auth/me
        if path == "/api/auth/me":
            try:
                _, user = self._authenticate_request()
                self._send_json({"user": asdict(user)})
            except Exception as e:
                self._send_error_json(str(e), status=401)
            return

        # 3. Patient Cohort: /api/patients
        if path == "/api/patients":
            try:
                _, user = self._authenticate_request()
                patients = self.patient_service.list_patients(user, client_ip=client_ip)
                self._send_json(patients)
            except Exception as e:
                self._send_error_json(str(e), status=403)
            return

        # 4. Single Patient: /api/patients/<id>
        if path.startswith("/api/patients/") and not path.endswith("/fhir-export"):
            patient_id = path.split("/")[-1].upper()
            try:
                _, user = self._authenticate_request()
                patient = self.patient_service.get_patient(user, patient_id, client_ip=client_ip)
                self._send_json(patient)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except KeyError as e:
                self._send_error_json(str(e), status=404)
            return

        # 5. FHIR Export: /api/patients/<id>/fhir-export
        if path.startswith("/api/patients/") and path.endswith("/fhir-export"):
            parts = path.split("/")
            patient_id = parts[-2].upper()
            try:
                _, user = self._authenticate_request()
                bundle = self.patient_service.export_fhir_bundle(user, patient_id, client_ip=client_ip)
                self._send_json(bundle)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except FileNotFoundError as e:
                self._send_error_json(str(e), status=404)
            return

        # 6. Check-in Status: /api/checkins/status
        if path == "/api/checkins/status":
            try:
                _, user = self._authenticate_request()
                pid = query.get("patient_id", [user.patient_id or "P001"])[0]
                status_info = self.checkin_service.get_patient_checkin_status(pid)
                self._send_json(status_info)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 7. Check-in Questions: /api/checkins/questions
        if path == "/api/checkins/questions":
            self._send_json(self.checkin_service.get_questions_schema())
            return

        # 8. Check-in Calendar: /api/checkins/calendar
        if path == "/api/checkins/calendar":
            try:
                _, user = self._authenticate_request()
                pid = query.get("patient_id", [user.patient_id or "P001"])[0]
                cal = self.checkin_service.get_patient_calendar(pid)
                self._send_json(cal)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 9. Missed Check-ins: /api/checkins/missed
        if path == "/api/checkins/missed":
            try:
                _, user = self._authenticate_request()
                if user.role == Role.PATIENT:
                    self._send_error_json("Unauthorized", status=403)
                    return
                self._send_json(self.checkin_service.list_missed_checkins())
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 10. Clinical Alerts: /api/alerts
        if path == "/api/alerts":
            try:
                _, user = self._authenticate_request()
                alerts = self.alert_service.list_alerts(user, client_ip=client_ip)
                self._send_json(alerts)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 11. Smartwatch Alert Feed: /api/alerts/smartwatch-feed
        if path == "/api/alerts/smartwatch-feed":
            try:
                _, user = self._authenticate_request()
                feed = self.alert_service.get_smartwatch_feed(user, client_ip=client_ip)
                self._send_json(feed)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 12. Digital Twin: /api/digital-twin/<id>
        if path.startswith("/api/digital-twin/"):
            patient_id = path.split("/")[-1].upper()
            try:
                _, user = self._authenticate_request()
                twin = self.digital_twin_service.get_digital_twin(user, patient_id, client_ip=client_ip)
                self._send_json(twin)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except KeyError as e:
                self._send_error_json(str(e), status=404)
            return

        # 13. Clinical Notes: /api/clinical/notes
        if path == "/api/clinical/notes":
            try:
                _, user = self._authenticate_request()
                pid = query.get("patient_id", ["P001"])[0]
                notes = self.clinical_service.get_notes(user, pid, client_ip=client_ip)
                self._send_json(notes)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 14. Senior Doctor Oversight Metrics: /api/oversight/metrics
        if path == "/api/oversight/metrics":
            try:
                _, user = self._authenticate_request()
                metrics = self.clinical_service.get_oversight_metrics(user, client_ip=client_ip)
                self._send_json(metrics)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 15. Audit Log: /api/oversight/audit-log
        if path == "/api/oversight/audit-log":
            try:
                _, user = self._authenticate_request()
                if not self.rbac.check_permission(user, Permission.VIEW_AUDIT_LOGS, client_ip=client_ip):
                    self._send_error_json("Forbidden: Senior Doctor authority required", status=403)
                    return
                logs = self.rbac.audit.get_entries(limit=100)
                self._send_json(logs)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 404 Fallback
        self._send_error_json(f"Endpoint not found: {path}", status=404)

    # ----------------------------------------------------
    # POST ROUTER
    # ----------------------------------------------------
    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        client_ip = self.client_address[0]

        # 1. Login: /api/auth/login
        if path == "/api/auth/login":
            try:
                body = self._read_body_json()
                username = body.get("username", "nurse_sarah")
                session, user = self.rbac.authenticate(username, client_ip=client_ip)
                cookie_str = f"hg_session={session.token}; Path=/; HttpOnly; SameSite=Lax"
                self._send_json(
                    {"token": session.token, "user": asdict(user)},
                    headers={"Set-Cookie": cookie_str},
                )
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 2. Logout: /api/auth/logout
        if path == "/api/auth/logout":
            token = self._get_auth_token()
            if token:
                self.rbac.logout(token)
            cookie_str = "hg_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            self._send_json({"logged_out": True}, headers={"Set-Cookie": cookie_str})
            return

        # 3. Check-in Submission: /api/checkins/submit
        if path == "/api/checkins/submit":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                pid = body.get("patient_id") or user.patient_id or "P001"
                answers = body.get("answers", {})
                voice_note = body.get("voice_note")
                sub = self.checkin_service.submit_checkin(
                    pid, answers, voice_note=voice_note, user=user, client_ip=client_ip
                )
                self._send_json(asdict(sub))
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 4. Acknowledge Alert: /api/alerts/acknowledge
        if path == "/api/alerts/acknowledge":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                alert_id = body.get("alert_id")
                notes = body.get("notes")
                alert = self.alert_service.acknowledge_alert(user, alert_id, notes=notes, client_ip=client_ip)
                self._send_json(asdict(alert))
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 5. Escalate Alert: /api/alerts/escalate
        if path == "/api/alerts/escalate":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                alert_id = body.get("alert_id")
                role = body.get("escalate_to_role", "doctor")
                reason = body.get("reason", "Clinical escalation")
                alert = self.alert_service.escalate_alert(user, alert_id, role, reason, client_ip=client_ip)
                self._send_json(asdict(alert))
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 6. Add Clinical Note: /api/clinical/notes
        if path == "/api/clinical/notes":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                pid = body.get("patient_id", "P001")
                note_type = body.get("note_type", "NURSE_OBSERVATION")
                content = body.get("content", "")
                note = self.clinical_service.add_note(user, pid, note_type, content, client_ip=client_ip)
                self._send_json(asdict(note))
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 7. Senior Doctor Reassign Patient: /api/oversight/reassign
        if path == "/api/oversight/reassign":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                pid = body.get("patient_id")
                staff = body.get("assignee_user_id")
                reason = body.get("reason", "Staff workload adjustment")
                res = self.rbac.reassign_patient(user, pid, staff, reason, client_ip=client_ip)
                self._send_json(res)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 8. Senior Doctor Workflow Override: /api/oversight/override
        if path == "/api/oversight/override":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                pid = body.get("patient_id")
                override_type = body.get("override_type", "IMMEDIATE_ESCALATION")
                reason = body.get("reason", "Critical clinical decompensation")
                res = self.rbac.override_workflow(user, pid, override_type, reason, client_ip=client_ip)
                self._send_json(res)
            except PermissionError as e:
                self._send_error_json(str(e), status=403)
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        # 9. Voice Command Processing: /api/voice/process
        if path == "/api/voice/process":
            try:
                _, user = self._authenticate_request()
                body = self._read_body_json()
                raw_text = body.get("text", "")
                res = self.voice_service.parse_command(raw_text, user)
                self._send_json(asdict(res))
            except Exception as e:
                self._send_error_json(str(e), status=400)
            return

        self._send_error_json(f"POST endpoint not found: {path}", status=404)


def create_healthguard_server(
    root_dir: Path, host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    rbac = RBACManager(audit_path=root_dir / "data" / "audit_log.json")
    patient_service = PatientService(root_dir, rbac)
    checkin_service = CheckinService(rbac=rbac)
    alert_service = AlertService(rbac)
    digital_twin_service = DigitalTwinService(root_dir, rbac)
    clinical_service = ClinicalService(rbac)
    voice_service = VoiceService(rbac)

    # Attach services to handler class
    class BoundHandler(HealthGuardAPIHandler):
        pass

    BoundHandler.rbac = rbac
    BoundHandler.patient_service = patient_service
    BoundHandler.checkin_service = checkin_service
    BoundHandler.alert_service = alert_service
    BoundHandler.digital_twin_service = digital_twin_service
    BoundHandler.clinical_service = clinical_service
    BoundHandler.voice_service = voice_service

    server = ThreadingHTTPServer((host, port), BoundHandler)
    return server
