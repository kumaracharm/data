from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.config import DATA
from simulator.patient_loader import load_patients


def main() -> None:
    patients = load_patients(expected_count=10)
    import_manifest = json.loads(
        (DATA / "synthea_original" / "cohort_import_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if import_manifest.get("distinct_fhir_patient_resources") != 10:
        raise ValueError("Cohort import manifest does not confirm ten distinct FHIR Patient IDs.")
    result = {
        "verified_registry_profile_fhir_pairs": len(patients),
        "patient_ids": [patient.patient_id for patient in patients],
        "distinct_fhir_patient_resources": len(
            {patient.fhir_patient_id for patient in patients}
        ),
        "spo2_scales": {patient.patient_id: patient.spo2_scale for patient in patients},
        "historical_observation_counts_before_encounter_end": {
            patient.patient_id: len(patient.historical_observations) for patient in patients
        },
        "patients_without_historical_vital_loinc_codes": [
            patient.patient_id
            for patient in patients
            if not patient.historical_vital_loinc_codes
        ],
        "initial_vitals": "PENDING; provisional demo values are configured separately.",
        "candidate_population_count": import_manifest.get("selection_population_count"),
        "candidate_population_count_verifiable": import_manifest.get(
            "selection_population_count_verifiable_from_archive"
        ),
        "cohort_readme": import_manifest.get("cohort_readme"),
        "clinical_validation": False,
    }
    output = DATA / "simulation" / "cohort_validation.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
