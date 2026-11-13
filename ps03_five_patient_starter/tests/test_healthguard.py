from __future__ import annotations

import unittest
from pathlib import Path

from healthguard.alert_service import AlertPriority, AlertService, AlertStatus
from healthguard.checkin_service import CheckinService
from healthguard.clinical_service import ClinicalService
from healthguard.digital_twin_service import DigitalTwinService
from healthguard.patient_service import PatientService
from healthguard.rbac import Permission, RBACManager, Role, User
from healthguard.voice_service import VoiceService

ROOT = Path(__file__).resolve().parents[1]


class HealthGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rbac = RBACManager()
        self.patient_service = PatientService(ROOT, self.rbac)
        self.checkin_service = CheckinService(rbac=self.rbac)
        self.alert_service = AlertService(self.rbac)
        self.digital_twin_service = DigitalTwinService(ROOT, self.rbac)
        self.clinical_service = ClinicalService(self.rbac)
        self.voice_service = VoiceService(self.rbac)

        # Users for testing
        self.patient_p001 = self.rbac.get_user("patient_p001")
        self.patient_p003 = self.rbac.get_user("patient_p003")
        self.pheon = self.rbac.get_user("pheon_marcus")
        self.nurse = self.rbac.get_user("nurse_sarah")
        self.doctor = self.rbac.get_user("dr_chen")
        self.senior_doctor = self.rbac.get_user("dr_patel")

    # 1. Patient cannot access another patient's data
    def test_01_patient_cannot_access_other_patient_data(self) -> None:
        # P001 can access own data
        p1 = self.patient_service.get_patient(self.patient_p001, "P001")
        self.assertEqual(p1["patient_id"], "P001")

        # P001 accessing P003 MUST be rejected
        with self.assertRaises(PermissionError):
            self.patient_service.get_patient(self.patient_p001, "P003")

        # Digital twin access to another patient MUST be rejected
        with self.assertRaises(PermissionError):
            self.digital_twin_service.get_digital_twin(self.patient_p001, "P003")

    # 2. PHEON sees assigned patients only
    def test_02_pheon_sees_assigned_patients_only(self) -> None:
        patients = self.patient_service.list_patients(self.pheon)
        patient_ids = [p["patient_id"] for p in patients]

        # Marcus is assigned P001, P002, P003, P007
        self.assertEqual(set(patient_ids), {"P001", "P002", "P003", "P007"})
        self.assertNotIn("P004", patient_ids)

        # Attempting direct access to unassigned P004
        with self.assertRaises(PermissionError):
            self.patient_service.get_patient(self.pheon, "P004")

    # 3. Nurse sees authorized clinical information & can acknowledge alerts
    def test_03_nurse_sees_authorized_clinical_info(self) -> None:
        p1 = self.patient_service.get_patient(self.nurse, "P001")
        self.assertIn("conditions", p1)
        self.assertIn("medications", p1)

        # Nurse can add clinical observation
        note = self.clinical_service.add_note(
            self.nurse, "P001", "NURSE_OBSERVATION", "Vital signs stable during shift review."
        )
        self.assertEqual(note.author_role, "nurse")
        self.assertEqual(note.patient_id, "P001")

        # Nurse can acknowledge alerts
        alert = self.alert_service.create_alert(
            patient_id="P001",
            patient_name="Clora Christiansen",
            priority=AlertPriority.P2,
            vital_name="Heart Rate",
            measured_value=105.0,
            unit="bpm",
            threshold="> 100",
            trigger_reason="Mild elevated rate",
            recommended_action="Recheck after rest",
        )
        acked = self.alert_service.acknowledge_alert(self.nurse, alert.alert_id, notes="Nurse verified resting state")
        self.assertEqual(acked.status, AlertStatus.ACKNOWLEDGED)

    # 4. Doctor can review relevant patient history & add assessments
    def test_04_doctor_can_review_history_and_notes(self) -> None:
        p3 = self.patient_service.get_patient(self.doctor, "P003")
        self.assertEqual(p3["patient_id"], "P003")
        self.assertTrue(len(p3["conditions"]) > 0)

        # Doctor can add physician assessment
        note = self.clinical_service.add_note(
            self.doctor, "P003", "PHYSICIAN_ASSESSMENT", "Compensated heart failure; diuretics increased."
        )
        self.assertEqual(note.author_role, "doctor")

        # Doctor can create care plan
        plan = self.clinical_service.create_care_plan(
            self.doctor,
            "P003",
            {
                "target_spo2": ">= 92%",
                "target_hr": "70-85 bpm",
                "medication_orders": ["Furosemide 40mg daily"],
                "checkin_frequency": "TWICE_DAILY",
                "followup_date": "In 3 days",
                "instructions": "Daily weights before breakfast",
            },
        )
        self.assertEqual(plan.patient_id, "P003")

    # 5. Senior doctor can access authorized oversight features
    def test_05_senior_doctor_accesses_oversight_and_governance(self) -> None:
        # Senior doctor sees all 10 patients
        all_patients = self.patient_service.list_patients(self.senior_doctor)
        self.assertEqual(len(all_patients), 10)

        # Senior doctor can access oversight metrics
        metrics = self.clinical_service.get_oversight_metrics(self.senior_doctor)
        self.assertEqual(metrics["total_cohort_patients"], 10)
        self.assertIn("staff_performance", metrics)

        # Non-senior doctor cannot access oversight metrics
        with self.assertRaises(PermissionError):
            self.clinical_service.get_oversight_metrics(self.doctor)

        # Senior doctor can reassign patients
        reassign_res = self.rbac.reassign_patient(
            self.senior_doctor, "P004", "nurse_sarah", "Workload rebalancing"
        )
        self.assertEqual(reassign_res["patient_id"], "P004")
        self.assertIn("P004", self.nurse.assigned_patients)

        # Senior doctor can override workflow with audit reason
        override_res = self.rbac.override_workflow(
            self.senior_doctor, "P003", "IMMEDIATE_ICU_ESCALATION", "Rapid clinical deterioration"
        )
        self.assertEqual(override_res["patient_id"], "P003")

    # 6. Unauthorized FHIR exports are rejected; authorized exports succeed
    def test_06_unauthorized_fhir_exports_are_rejected(self) -> None:
        # Patient cannot export FHIR
        with self.assertRaises(PermissionError):
            self.patient_service.export_fhir_bundle(self.patient_p001, "P001")

        # PHEON cannot export FHIR
        with self.assertRaises(PermissionError):
            self.patient_service.export_fhir_bundle(self.pheon, "P001")

        # Doctor CAN export FHIR for assigned patient
        bundle = self.patient_service.export_fhir_bundle(self.doctor, "P001")
        self.assertIn("resourceType", bundle)
        self.assertEqual(bundle["resourceType"], "Bundle")

        # Audit log has recorded export actions
        audit_entries = self.rbac.audit.get_entries(limit=10)
        export_logs = [e for e in audit_entries if "FHIR_EXPORT" in e["action"]]
        self.assertTrue(len(export_logs) > 0)

    # 7. Daily check-in saves correctly and scores clinical risk
    def test_07_daily_checkin_saves_correctly(self) -> None:
        answers = {
            "q1_overall_feeling": "unwell",
            "q2_breathing": "severe",
            "q3_fatigue": "mild",
            "q4_fever_discomfort": "none",
            "q5_medication": "partial",
            "q6_new_symptoms": ["cough"],
            "adaptive_onset": "today",
            "adaptive_progression": "rapidly_worse",
            "adaptive_activity_impact": "severe_impact",
        }
        submission = self.checkin_service.submit_checkin(
            patient_id="P001",
            answers=answers,
            voice_note="Feeling breathless when walking to kitchen",
            user=self.patient_p001,
        )
        self.assertEqual(submission.patient_id, "P001")
        self.assertTrue(submission.total_risk_score >= 8)
        self.assertEqual(submission.clinical_risk_tier, "CRITICAL")
        self.assertTrue(submission.alert_triggered)

        # Status is updated
        status = self.checkin_service.get_patient_checkin_status("P001")
        self.assertTrue(status["is_completed_today"])

    # 8. Missed check-in detection works
    def test_08_missed_checkin_detection(self) -> None:
        missed = self.checkin_service.list_missed_checkins()
        self.assertIsInstance(missed, list)
        if missed:
            first = missed[0]
            self.assertIn("patient_id", first)
            self.assertIn("overdue_hours", first)

    # 9. Critical alerts escalate in parallel without waiting for routine chain
    def test_09_critical_alerts_escalate_in_parallel(self) -> None:
        p0_alert = self.alert_service.create_alert(
            patient_id="P009",
            patient_name="Jarrett Mraz",
            priority=AlertPriority.P0,
            vital_name="SpO2",
            measured_value=85.0,
            unit="%",
            threshold="< 88%",
            trigger_reason="Severe hypoxemia post-NSTEMI",
            recommended_action="Immediate emergency oxygen titration and physician review.",
        )
        self.assertEqual(p0_alert.priority, AlertPriority.P0)
        self.assertTrue(p0_alert.is_parallel_escalation)
        self.assertIn("doctor", p0_alert.notified_roles)
        self.assertIn("senior_doctor", p0_alert.notified_roles)
        self.assertIn("nurse", p0_alert.notified_roles)

    # 10. Stale wearable readings are visibly identified
    def test_10_stale_wearable_readings_visibly_identified(self) -> None:
        twin = self.digital_twin_service.get_digital_twin(self.doctor, "P003")
        self.assertIn("wearable", twin)
        self.assertIn("connection_state", twin["wearable"])
        self.assertIn("is_stale_measurement", twin["wearable"])
        self.assertIn("timeline", twin)
        self.assertIn("vitals", twin)

    # 11. Voice commands respect permissions & require confirmation for actions
    def test_11_voice_commands_respect_permissions(self) -> None:
        # Patient cannot query staff-level urgent patients
        res_pat = self.voice_service.parse_command("Show urgent patients", self.patient_p001)
        self.assertFalse(res_pat.authorized)

        # Clinician CAN query urgent patients
        res_nurse = self.voice_service.parse_command("Show urgent patients", self.nurse)
        self.assertTrue(res_nurse.authorized)
        self.assertEqual(res_nurse.action_type, "FILTER")

        # Clinician navigating to patient
        res_open = self.voice_service.parse_command("Open patient P003", self.doctor)
        self.assertTrue(res_open.authorized)
        self.assertEqual(res_open.parameters["patient_id"], "P003")

        # Sensitive action (Acknowledge alert) requires clinical confirmation
        res_ack = self.voice_service.parse_command("Acknowledge this alert", self.nurse)
        self.assertTrue(res_ack.authorized)
        self.assertTrue(res_ack.requires_confirmation)
        self.assertIsNotNone(res_ack.confirmation_message)

    # 12. Smartwatch alert format has privacy lock screen text
    def test_12_smartwatch_alert_format_has_privacy(self) -> None:
        feed = self.alert_service.get_smartwatch_feed(self.nurse)
        self.assertTrue(len(feed) > 0)
        first = feed[0]
        self.assertIn("lock_screen_text", first)
        self.assertIn("measurement", first)
        self.assertIn("recommended_action", first)
        self.assertIn("deep_link", first)

    # 13. Existing simulator engines remain operational
    def test_13_existing_simulator_remains_operational(self) -> None:
        from simulator.scenario_engine import ScenarioEngine
        from simulator.physiological_engine import PhysiologicalEngine
        from simulator.config import VitalBaseline, PatientSimulationConfig

        scenario = ScenarioEngine("P001", "gradual_deterioration", 5, 60)
        step = scenario.step(20)
        self.assertEqual(step.name, "gradual_deterioration")

        baseline = VitalBaseline(72.0, 97.0, 16.0, 36.8)
        sim_config = PatientSimulationConfig(
            patient_id="P001",
            seed=42,
            baseline=baseline,
            sampling_interval_seconds=1.0,
            scenario="gradual_deterioration",
        )
        engine = PhysiologicalEngine(config=sim_config)
        truth = engine.step(20, step.physiological_changes)
        self.assertTrue(truth["true_hr_bpm"] > 0)
        self.assertTrue(truth["true_spo2_percent"] > 0)


if __name__ == "__main__":
    unittest.main()
