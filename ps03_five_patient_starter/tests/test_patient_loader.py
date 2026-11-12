from __future__ import annotations

import unittest
from datetime import datetime
import json
import tempfile
from pathlib import Path

from simulator.config import load_patient_baseline, load_provisional_baseline
from simulator.patient_loader import load_patients
from simulator.simulation_manager import SimulationManager


class PatientLoaderTests(unittest.TestCase):
    def test_loads_ten_verified_profile_and_fhir_pairs(self) -> None:
        patients = load_patients()
        self.assertEqual(len(patients), 10)
        self.assertEqual([patient.patient_id for patient in patients], [f"P{i:03}" for i in range(1, 11)])
        self.assertTrue(all(patient.spo2_scale == 1 for patient in patients))
        self.assertTrue(all(patient.fhir_bundle_path.is_file() for patient in patients))

    def test_historical_vitals_are_separate_and_never_used_as_baseline(self) -> None:
        patients = load_patients()
        by_id = {patient.patient_id: patient for patient in patients}
        self.assertEqual(by_id["P007"].historical_vital_loinc_codes, ())
        self.assertEqual(by_id["P008"].historical_vital_loinc_codes, ())
        for patient in patients:
            self.assertTrue(
                all(
                    datetime.fromisoformat(observation["timestamp"])
                    <= patient.encounter_end
                    for observation in patient.historical_observations
                )
            )
        baseline = load_provisional_baseline()
        manager = SimulationManager(profiles=patients, baseline=baseline)
        self.assertEqual(manager.patient_states["P001"].spo2, 97.0)
        self.assertEqual(manager.patient_states["P003"].spo2, 97.0)
        self.assertEqual(
            manager.patient_states["P001"].baseline.provenance,
            "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC",
        )
        for patient_id in ("P001", "P003"):
            historical_spo2 = [
                value["value"]
                for observation in by_id[patient_id].historical_observations
                for value in observation["values"]
                if value.get("value") is not None
                and any(code.get("code") == "59408-5" for code in observation["codes"])
            ]
            self.assertTrue(historical_spo2)
            self.assertLess(min(historical_spo2), baseline.spo2_percent)
            self.assertEqual(
                manager.patient_states[patient_id].spo2,
                baseline.spo2_percent,
            )

    def test_medication_context_remains_historical(self) -> None:
        patient = {item.patient_id: item for item in load_patients()}["P002"]
        self.assertTrue(patient.historical_prescription_labels)
        self.assertTrue(all(isinstance(item, str) for item in patient.historical_prescription_labels))

    def test_fhir_observation_subject_and_encounter_references_resolve(self) -> None:
        for patient in load_patients():
            with self.subTest(patient=patient.patient_id):
                bundle = json.loads(patient.fhir_bundle_path.read_text(encoding="utf-8"))
                entries = bundle.get("entry", [])
                by_full_url = {
                    entry.get("fullUrl"): entry.get("resource", {})
                    for entry in entries
                    if entry.get("fullUrl")
                }
                by_type_and_id = {
                    (entry.get("resource", {}).get("resourceType"),
                     entry.get("resource", {}).get("id")): entry.get("resource", {})
                    for entry in entries
                }
                expected_patient = by_type_and_id[("Patient", patient.fhir_patient_id)]
                target_encounter = by_type_and_id[("Encounter", patient.encounter_id)]
                self.assertEqual(
                    target_encounter.get("subject", {}).get("reference"),
                    f"urn:uuid:{patient.fhir_patient_id}",
                )
                observations = [
                    entry["resource"]
                    for entry in entries
                    if entry.get("resource", {}).get("resourceType") == "Observation"
                ]
                self.assertTrue(observations)
                for observation in observations:
                    subject_reference = observation.get("subject", {}).get("reference")
                    subject = by_full_url.get(subject_reference)
                    if subject is None and subject_reference:
                        subject = by_type_and_id.get(
                            ("Patient", subject_reference.rsplit("/", 1)[-1])
                        )
                    self.assertEqual(subject, expected_patient)

                    encounter_reference = observation.get("encounter", {}).get("reference")
                    encounter = by_full_url.get(encounter_reference)
                    if encounter is None and encounter_reference:
                        encounter = by_type_and_id.get(
                            ("Encounter", encounter_reference.rsplit("/", 1)[-1])
                        )
                    self.assertIsNotNone(encounter)
                    self.assertEqual(
                        encounter.get("subject", {}).get("reference"),
                        f"urn:uuid:{patient.fhir_patient_id}",
                    )

    def test_patient_keyed_initial_value_override_is_still_nonclinical(self) -> None:
        contents = {
            "configuration_status": "PROVISIONAL_DEMO_VALUES_NOT_PATIENT_SPECIFIC",
            "initial_values": {
                "hr_bpm": 72,
                "spo2_percent": 97,
                "rr_bpm": 16,
                "temp_c": 36.8,
            },
            "patient_overrides": {
                "P001": {
                    "hr_bpm": 70,
                    "spo2_percent": 96,
                    "rr_bpm": 15,
                    "temp_c": 36.7,
                }
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "initial_values.json"
            path.write_text(json.dumps(contents), encoding="utf-8")
            baseline = load_patient_baseline("P001", path)
        self.assertEqual(baseline.hr_bpm, 70)
        self.assertEqual(
            baseline.provenance,
            "PROVISIONAL_PATIENT_KEYED_DEMO_OVERRIDE_NOT_CLINICAL",
        )


if __name__ == "__main__":
    unittest.main()
