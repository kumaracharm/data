from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXPECTED_PATIENT_IDS = {f"P{number:03d}" for number in range(1, 11)}
EXPECTED_MEMBERS = {
    *(f"original_fhir/{patient_id}.json" for patient_id in EXPECTED_PATIENT_IDS),
    *(f"monitoring_profiles/{patient_id}.json" for patient_id in EXPECTED_PATIENT_IDS),
    "patient_registry.json",
    "README.txt",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and verify the revised Synthea cohort ZIP.")
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path.home() / "Downloads" / "synthea_cohort_v2.zip",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing cohort file only when its archived contents differ.",
    )
    args = parser.parse_args()
    if not args.archive.is_file():
        raise FileNotFoundError(f"Required revised cohort ZIP not found: {args.archive}")

    with zipfile.ZipFile(args.archive) as archive:
        contents: dict[str, bytes] = {}
        for info in archive.infolist():
            member_name = info.filename.replace("\\", "/")
            member_path = PurePosixPath(member_name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Unsafe path in cohort ZIP: {info.filename}")
            if info.is_dir():
                continue
            if member_name in contents:
                raise ValueError(f"Duplicate path in cohort ZIP: {member_name}")
            contents[member_name] = archive.read(info)
        if set(contents) != EXPECTED_MEMBERS:
            missing = sorted(EXPECTED_MEMBERS - set(contents))
            unexpected = sorted(set(contents) - EXPECTED_MEMBERS)
            raise ValueError(
                f"Unexpected cohort archive layout; missing={missing}, unexpected={unexpected}"
            )

        registry = json.loads(contents["patient_registry.json"])
        if not isinstance(registry, list) or {
            entry.get("patient_id") for entry in registry
        } != EXPECTED_PATIENT_IDS:
            raise ValueError("patient_registry.json must contain exactly P001 through P010.")

        imported: dict[str, str] = {}
        patient_resource_ids: set[str] = set()
        for patient_id in sorted(EXPECTED_PATIENT_IDS):
            profile = json.loads(contents[f"monitoring_profiles/{patient_id}.json"])
            bundle = json.loads(contents[f"original_fhir/{patient_id}.json"])
            patient_resources = [
                entry.get("resource", {})
                for entry in bundle.get("entry", [])
                if entry.get("resource", {}).get("resourceType") == "Patient"
            ]
            if len(patient_resources) != 1:
                raise ValueError(f"{patient_id}: expected exactly one FHIR Patient resource.")
            fhir_patient_id = patient_resources[0].get("id")
            registry_record = next(
                item for item in registry if item["patient_id"] == patient_id
            )
            if (
                profile.get("patient_id") != patient_id
                or profile.get("fhir_patient_id") != fhir_patient_id
                or registry_record.get("fhir_patient_id") != fhir_patient_id
            ):
                raise ValueError(f"{patient_id}: profile, registry, and FHIR Patient IDs do not match.")
            if fhir_patient_id in patient_resource_ids:
                raise ValueError(f"Duplicate Synthea Patient resource ID: {fhir_patient_id}")
            patient_resource_ids.add(fhir_patient_id)
            imported[patient_id] = fhir_patient_id

        for member_name, payload in contents.items():
            member_path = PurePosixPath(member_name)
            if member_path.parts[0] == "original_fhir":
                destination = DATA / "synthea_original" / "original_fhir" / member_path.name
            elif member_path.parts[0] == "monitoring_profiles":
                destination = DATA / "monitoring_profiles" / member_path.name
            else:
                destination = DATA / "synthea_original" / member_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and sha256_bytes(destination.read_bytes()) != sha256_bytes(payload):
                if not args.force:
                    raise FileExistsError(
                        f"Different cohort file already exists at {destination}; "
                        "review it or rerun with --force."
                    )
            destination.write_bytes(payload)

    archive_digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    readme = contents["README.txt"].decode("utf-8", "replace")
    manifest = {
        "archive_filename": args.archive.name,
        "archive_sha256": archive_digest,
        "patient_count": 10,
        "patient_ids": sorted(EXPECTED_PATIENT_IDS),
        "registry_and_fhir_patient_ids_match": True,
        "distinct_fhir_patient_resources": len(patient_resource_ids),
        "verified_fhir_patient_ids": imported,
        "selection_population_count": None,
        "selection_population_count_verifiable_from_archive": False,
        "selection_population_provenance_note": (
            "The supplied cohort archive does not include evidence of the size of the "
            "candidate population it was selected from."
        ),
        "cohort_readme": readme,
    }
    manifest_path = DATA / "synthea_original" / "cohort_import_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Imported and verified {len(imported)} unique Synthea Patient resources.")
    print(f"Archive SHA-256: {archive_digest}")
    print(f"Import manifest: {manifest_path}")
    print("Candidate-population selection count is not verifiable from the supplied archive.")


if __name__ == "__main__":
    main()
