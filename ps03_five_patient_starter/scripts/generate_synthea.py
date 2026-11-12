from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import ijson


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "synthea_original"
JAR_PATH = OUT / "synthea-with-dependencies.jar"
JAR_URL = (
    "https://github.com/synthetichealth/synthea/releases/download/"
    "master-branch-latest/synthea-with-dependencies.jar"
)
JAR_SHA256 = "20098ec70c24c715ea874b19140983634711cc4986196105b5b0a9ca72a4b644"
SCENARIOS = {
    "P001": None,
    "P002": {
        "label": "COPD",
        "terms": ("copd", "chronic obstructive bronchitis", "pulmonary emphysema"),
        "codes": ("J44", "185086009", "87433001"),
    },
    "P003": {
        "label": "heart failure",
        "terms": ("heart failure",),
        "codes": ("I50",),
    },
    "P004": {
        "label": "pneumonia",
        "terms": ("pneumonia",),
        "codes": ("J12", "J13", "J14", "J15", "J16", "J17", "J18"),
    },
    "P005": {
        "label": "sepsis",
        "terms": ("sepsis",),
        "codes": ("A40", "A41", "R65.2"),
    },
}
SEED = 20261112
INITIAL_POPULATION = 200
MAX_POPULATION = 800


def resource_display(resource: dict[str, Any]) -> str:
    concept = resource.get("code") or resource.get("medicationCodeableConcept") or {}
    if isinstance(concept, dict):
        if concept.get("text"):
            return str(concept["text"])
        for coding in concept.get("coding", []):
            if coding.get("display"):
                return str(coding["display"])
            if coding.get("code"):
                return str(coding["code"])
    return "Not specified in resource"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def patient_summary(bundle_path: Path) -> dict[str, Any] | None:
    patients: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    medications: list[dict[str, Any]] = []
    with bundle_path.open("rb") as source:
        for resource in ijson.items(source, "entry.item.resource"):
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("resourceType")
            if resource_type == "Patient":
                patients.append(resource)
            elif resource_type == "Condition":
                conditions.append(resource)
            elif resource_type in {
                "MedicationRequest",
                "MedicationStatement",
                "MedicationAdministration",
            }:
                medications.append(resource)
    if not patients:
        return None
    if len(patients) != 1:
        raise ValueError(f"Expected one FHIR Patient in generated bundle {bundle_path}")
    return {
        "bundle_path": str(bundle_path),
        "bundle_size": bundle_path.stat().st_size,
        "patient": patients[0],
        "conditions": conditions,
        "medications": medications,
    }


def condition_matches(condition: dict[str, Any], rule: dict[str, Any]) -> bool:
    concept = condition.get("code", {})
    text = " ".join(
        [
            str(concept.get("text", "")),
            *[
                f"{coding.get('display', '')} {coding.get('code', '')}"
                for coding in concept.get("coding", [])
            ],
        ]
    ).casefold()
    return any(term in text for term in rule["terms"]) or any(
        str(coding.get("code", "")).startswith(rule_code)
        for coding in concept.get("coding", [])
        for rule_code in rule["codes"]
    )


def safe_bundle_summary(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as source:
            resource_type = next(ijson.items(source, "resourceType"), None)
    except (OSError, ijson.JSONError) as error:
        raise ValueError(f"Could not read generated FHIR bundle {path}: {error}") from error
    if resource_type != "Bundle":
        raise ValueError(f"Expected a FHIR Bundle in generated output {path}")
    summary = patient_summary(path)
    if summary is None:
        return None
    return summary


def choose_profiles(bundles: list[dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
    selected: dict[str, dict[str, Any]] = {}
    used_patient_ids: set[str] = set()

    for patient_id, rule in list(SCENARIOS.items())[1:]:
        candidates = [
            bundle
            for bundle in bundles
            if str(bundle["patient"].get("id")) not in used_patient_ids
            and bundle["medications"]
            and any(condition_matches(condition, rule) for condition in bundle["conditions"])
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda bundle: (
                bundle["bundle_size"],
                -len(bundle["conditions"]),
                str(bundle["patient"].get("id", "")),
            )
        )
        chosen = candidates[0]
        selected[patient_id] = chosen
        used_patient_ids.add(str(chosen["patient"].get("id")))

    recovery_candidates = [
        bundle
        for bundle in bundles
        if str(bundle["patient"].get("id")) not in used_patient_ids
        and bundle["conditions"]
        and bundle["medications"]
    ]
    if not recovery_candidates:
        return None
    recovery_candidates.sort(
        key=lambda bundle: (
            bundle["bundle_size"],
            -len(bundle["conditions"]),
            str(bundle["patient"].get("id", "")),
        )
    )
    selected["P001"] = recovery_candidates[0]
    return {patient_id: selected[patient_id] for patient_id in SCENARIOS}


def find_java(explicit_java: str | None) -> str:
    candidates = [explicit_java, os.environ.get("JAVA_HOME"), shutil.which("java")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        executable = path / "bin" / ("java.exe" if os.name == "nt" else "java") if path.is_dir() else path
        if executable.is_file():
            return str(executable)
    raise FileNotFoundError(
        "Java 17+ was not found. Put a JDK on PATH, set JAVA_HOME, or pass --java <java.exe>."
    )


def ensure_jar() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if JAR_PATH.exists():
        actual_hash = sha256_file(JAR_PATH)
        if actual_hash != JAR_SHA256:
            raise ValueError(f"Unexpected Synthea JAR checksum at {JAR_PATH}: {actual_hash}")
        return

    temporary_path = JAR_PATH.with_suffix(".jar.part")
    request = urllib.request.Request(JAR_URL, headers={"User-Agent": "PS03-research-dataset"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response, temporary_path.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != JAR_SHA256:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Official Synthea JAR checksum mismatch: {digest.hexdigest()}")
    temporary_path.replace(JAR_PATH)


def run_population(java: str, output_dir: Path, population: int, seed: int, age_range: str) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    command = [
        java,
        "-jar",
        str(JAR_PATH),
        "-s",
        str(seed),
        "-p",
        str(population),
        "-a",
        age_range,
        f"--exporter.baseDirectory={output_dir}",
        "--exporter.fhir.export=true",
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def load_candidates(fhir_dir: Path) -> list[dict[str, Any]]:
    bundle_paths = sorted(fhir_dir.glob("*.json")) if fhir_dir.exists() else []
    if not bundle_paths:
        raise FileNotFoundError(f"Synthea produced no FHIR JSON bundles under {fhir_dir}")
    bundles = [
        summary
        for path in bundle_paths
        if (summary := safe_bundle_summary(path)) is not None
    ]
    if not bundles:
        raise ValueError(f"Synthea produced no patient FHIR bundles under {fhir_dir}")
    return bundles


def condition_record(resource: dict[str, Any]) -> dict[str, Any]:
    concept = resource.get("code", {})
    return {
        "resource_type": "Condition",
        "resource_id": resource.get("id"),
        "display": resource_display(resource),
        "coding": concept.get("coding", []),
        "clinical_status": resource.get("clinicalStatus"),
        "onset": resource.get("onsetDateTime") or resource.get("onsetPeriod"),
    }


def medication_record(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "resource_type": resource.get("resourceType"),
        "resource_id": resource.get("id"),
        "display": resource_display(resource),
        "status": resource.get("status"),
        "authored_on": resource.get("authoredOn"),
        "medication_reference": resource.get("medicationReference"),
    }


def build_selected_profiles(
    selected: dict[str, dict[str, Any]],
    seed: int,
    population: int,
    age_range: str,
) -> None:
    selected_dir = OUT / "selected_profiles"
    selected_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "generator": "Synthea",
        "release_tag": "master-branch-latest",
        "release_commit": "bd8a52ba0a54ad8c6a1004b74787a14d8c25dbcc",
        "jar_filename": JAR_PATH.name,
        "jar_sha256": JAR_SHA256,
        "jar_url": JAR_URL,
        "seed": seed,
        "candidate_population_size": population,
        "age_range": age_range,
        "fhir_export": True,
        "selections": {},
    }

    for patient_id, summary in selected.items():
        source_path = Path(summary["bundle_path"])
        selected_path = selected_dir / f"{patient_id}_{source_path.name}"
        shutil.copy2(source_path, selected_path)
        bundle_hash = sha256_file(selected_path)
        patient = summary["patient"]
        rule = SCENARIOS[patient_id]
        matched_conditions = [
            condition
            for condition in summary["conditions"]
            if rule is not None and condition_matches(condition, rule)
        ]
        manifest["selections"][patient_id] = {
            "synthea_patient_id": patient.get("id"),
            "source_bundle": str(selected_path.relative_to(ROOT)),
            "selected_bundle": str(selected_path.relative_to(ROOT)),
            "generated_candidate_filename": source_path.name,
            "selected_bundle_sha256": bundle_hash,
            "patient_resource_id": patient.get("id"),
            "conditions": [condition_record(item) for item in summary["conditions"]],
            "medications": [medication_record(item) for item in summary["medications"]],
            "matched_condition": (
                {
                    "selection_rule": rule["label"],
                    "resources": [condition_record(item) for item in matched_conditions],
                }
                if rule is not None
                else None
            ),
            "synthea_demographics": {
                "gender": patient.get("gender"),
                "birth_date": patient.get("birthDate"),
                "name": patient.get("name", []),
            },
        }

    (OUT / "selection.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def reusable_selection(seed: int, age_range: str) -> dict[str, Any] | None:
    selection_path = OUT / "selection.json"
    if not selection_path.is_file():
        return None
    try:
        manifest = json.loads(selection_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if (
        manifest.get("seed") != seed
        or manifest.get("age_range") != age_range
        or manifest.get("jar_sha256") != JAR_SHA256
        or set(manifest.get("selections", {})) != set(SCENARIOS)
    ):
        return None
    for selected in manifest["selections"].values():
        path = ROOT / selected.get("selected_bundle", "")
        if (
            not path.is_file()
            or selected.get("source_bundle") != selected.get("selected_bundle")
            or sha256_file(path) != selected.get("selected_bundle_sha256")
        ):
            return None
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and select five Synthea FHIR profiles.")
    parser.add_argument("--java", help="Path to Java 17+ executable or JDK directory.")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--population-size", type=int, default=INITIAL_POPULATION)
    parser.add_argument("--max-population", type=int, default=MAX_POPULATION)
    parser.add_argument("--age-range", default="50-90")
    parser.add_argument(
        "--reuse-candidates",
        action="store_true",
        help="Select from an existing candidate_output/fhir folder without rerunning Java.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Generate a fresh Synthea population instead of reusing verified selected bundles.",
    )
    args = parser.parse_args()

    if args.population_size < 5 or args.max_population < args.population_size:
        parser.error("--population-size must be >= 5 and <= --max-population")
    java = find_java(args.java)
    ensure_jar()

    output_dir = OUT / "candidate_output"
    if not args.force and not args.reuse_candidates:
        existing_selection = reusable_selection(args.seed, args.age_range)
        if existing_selection is not None:
            print("Reusing the five hash-verified original Synthea FHIR bundles.")
            for patient_id in SCENARIOS:
                entry = existing_selection["selections"][patient_id]
                print(
                    f"{patient_id}: Synthea Patient/{entry['synthea_patient_id']} "
                    f"({len(entry['conditions'])} conditions, "
                    f"{len(entry['medications'])} medication resources)"
                )
            return

    population = args.population_size
    if args.reuse_candidates:
        bundles = load_candidates(output_dir / "fhir")
        selected = choose_profiles(bundles)
        if selected is None:
            raise RuntimeError(
                "Existing Synthea output has no distinct matching five profiles with "
                "Condition and medication resources."
            )
    else:
        while True:
            print(f"Generating {population} Synthea candidates (seed {args.seed})...", flush=True)
            run_population(java, output_dir, population, args.seed, args.age_range)
            bundles = load_candidates(output_dir / "fhir")
            selected = choose_profiles(bundles)
            if selected is not None:
                break
            if population >= args.max_population:
                raise RuntimeError(
                    "Synthea candidate population did not contain distinct COPD, heart failure, "
                    "pneumonia, sepsis, and recovery profiles. Increase --max-population or "
                    "choose a different --seed; no conditions were fabricated."
                )
            population = min(population * 2, args.max_population)

    build_selected_profiles(selected, args.seed, population, args.age_range)
    shutil.rmtree(output_dir)
    print(
        "Selected exactly five Synthea Patient resources; original selected bundles retained "
        f"under {OUT / 'selected_profiles'}."
    )
    for patient_id in SCENARIOS:
        entry = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))["selections"][
            patient_id
        ]
        print(
            f"{patient_id}: Synthea Patient/{entry['synthea_patient_id']} "
            f"({len(entry['conditions'])} conditions, {len(entry['medications'])} medication resources)"
        )


if __name__ == "__main__":
    main()
