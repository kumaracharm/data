from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from healthguard.server import create_healthguard_server

ROOT = Path(__file__).resolve().parents[1]


class HealthGuardServerAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.port = 8765
        cls.server = create_healthguard_server(ROOT, host="127.0.0.1", port=cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
        token: str | None = None,
    ) -> tuple[int, dict | str]:
        url = f"{self.base_url}{endpoint}"
        body = json.dumps(data).encode("utf-8") if data else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                ct = resp.headers.get("Content-Type", "")
                if "application/json" in ct:
                    return status, json.loads(resp.read().decode("utf-8"))
                return status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                err_data = json.loads(err.read().decode("utf-8"))
            except Exception:
                err_data = {"error": "HTTPError"}
            return status, err_data

    def test_01_root_returns_html_app(self) -> None:
        status, html = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn("HEALTHGUARD", html)
        self.assertIn("Clinical Intelligence", html)

    def test_02_auth_login_and_me(self) -> None:
        status, data = self._request("/api/auth/login", method="POST", data={"username": "dr_chen"})
        self.assertEqual(status, 200)
        self.assertIn("token", data)
        self.assertEqual(data["user"]["role"], "doctor")
        token = data["token"]

        # Validate /api/auth/me with Bearer token
        status_me, data_me = self._request("/api/auth/me", token=token)
        self.assertEqual(status_me, 200)
        self.assertEqual(data_me["user"]["username"], "dr_chen")

    def test_03_patient_endpoint_rbac(self) -> None:
        # Login as patient_p001
        _, pat_data = self._request("/api/auth/login", method="POST", data={"username": "patient_p001"})
        pat_token = pat_data["token"]

        # P001 can view P001
        status, data = self._request("/api/patients/P001", token=pat_token)
        self.assertEqual(status, 200)
        self.assertEqual(data["patient_id"], "P001")

        # P001 cannot view P003 (returns 403 Forbidden)
        status_forbidden, _ = self._request("/api/patients/P003", token=pat_token)
        self.assertEqual(status_forbidden, 403)

    def test_04_fhir_export_rbac_enforcement(self) -> None:
        # Login as pheon_marcus
        _, pheon_data = self._request("/api/auth/login", method="POST", data={"username": "pheon_marcus"})
        pheon_token = pheon_data["token"]

        # PHEON attempting FHIR export returns 403 Forbidden
        status_pheon, _ = self._request("/api/patients/P001/fhir-export", token=pheon_token)
        self.assertEqual(status_pheon, 403)

        # Login as dr_chen
        _, doc_data = self._request("/api/auth/login", method="POST", data={"username": "dr_chen"})
        doc_token = doc_data["token"]

        # Doctor exporting FHIR succeeds
        status_doc, bundle = self._request("/api/patients/P001/fhir-export", token=doc_token)
        self.assertEqual(status_doc, 200)
        self.assertEqual(bundle.get("resourceType"), "Bundle")

    def test_05_checkin_flow_via_api(self) -> None:
        _, pat_data = self._request("/api/auth/login", method="POST", data={"username": "patient_p001"})
        pat_token = pat_data["token"]

        # Check questions schema
        status_q, q_data = self._request("/api/checkins/questions")
        self.assertEqual(status_q, 200)
        self.assertTrue(len(q_data["standard_questions"]) >= 6)

        # Submit checkin
        sub_payload = {
            "patient_id": "P001",
            "answers": {
                "q1_overall_feeling": "good",
                "q2_breathing": "none",
                "q3_fatigue": "none",
                "q4_fever_discomfort": "none",
                "q5_medication": "yes_all",
                "q6_new_symptoms": ["none"],
            },
        }
        status_sub, sub_res = self._request("/api/checkins/submit", method="POST", data=sub_payload, token=pat_token)
        self.assertEqual(status_sub, 200)
        self.assertEqual(sub_res["clinical_risk_tier"], "LOW")

        # Check updated status
        status_st, st_res = self._request("/api/checkins/status?patient_id=P001", token=pat_token)
        self.assertEqual(status_st, 200)
        self.assertTrue(st_res["is_completed_today"])

    def test_06_digital_twin_endpoint(self) -> None:
        _, doc_data = self._request("/api/auth/login", method="POST", data={"username": "dr_chen"})
        doc_token = doc_data["token"]

        status, twin = self._request("/api/digital-twin/P001", token=doc_token)
        self.assertEqual(status, 200)
        self.assertIn("wearable", twin)
        self.assertIn("vitals", twin)
        self.assertIn("observed_hr_bpm", twin["vitals"])

    def test_07_senior_doctor_oversight_endpoints(self) -> None:
        # Non-senior doctor gets 403 on oversight metrics
        _, doc_data = self._request("/api/auth/login", method="POST", data={"username": "dr_chen"})
        status_doc, _ = self._request("/api/oversight/metrics", token=doc_data["token"])
        self.assertEqual(status_doc, 403)

        # Senior doctor gets 200
        _, senior_data = self._request("/api/auth/login", method="POST", data={"username": "dr_patel"})
        senior_token = senior_data["token"]
        status_sen, metrics = self._request("/api/oversight/metrics", token=senior_token)
        self.assertEqual(status_sen, 200)
        self.assertEqual(metrics["total_cohort_patients"], 10)

    def test_08_voice_command_api(self) -> None:
        _, nurse_data = self._request("/api/auth/login", method="POST", data={"username": "nurse_sarah"})
        nurse_token = nurse_data["token"]

        status, voice_res = self._request(
            "/api/voice/process", method="POST", data={"text": "Show urgent patients"}, token=nurse_token
        )
        self.assertEqual(status, 200)
        self.assertTrue(voice_res["authorized"])
        self.assertEqual(voice_res["action_type"], "FILTER")


if __name__ == "__main__":
    unittest.main()
