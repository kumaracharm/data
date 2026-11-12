from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bidmc_original"
BASE_URL = "https://physionet.org/files/bidmc/1.0.0/"
RECORDINGS = {
    "P001": "01",
    "P002": "02",
    "P003": "03",
    "P004": "04",
    "P005": "05",
}
SOURCE_FILES = ("Numerics.csv", "Signals.csv", "Breaths.csv", "Fix.txt")
METADATA_FILES = ("LICENSE", "README", "RECORDS", "SHA256SUMS.txt")
EXPECTED_HEADERS = {
    "time_s": "time [s]",
    "hr_bpm": "hr",
    "rr_bpm": "resp",
    "spo2_percent": "spo2",
}


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "PS03-research-dataset"})
    with urllib.request.urlopen(request, timeout=60) as response:
        destination.write_bytes(response.read())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    checksums_path = OUT / "SHA256SUMS.txt"
    if not checksums_path.exists():
        download(BASE_URL + "SHA256SUMS.txt", checksums_path)

    checksums: dict[str, str] = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            checksums[parts[1].strip().lstrip("*")] = parts[0]

    for filename in METADATA_FILES:
        destination = OUT / filename
        if filename != "SHA256SUMS.txt" and not destination.exists():
            download(BASE_URL + filename, destination)

    header_mappings: dict[str, dict[str, str]] = {}
    for patient_id, recording in RECORDINGS.items():
        prefix = f"bidmc_{recording}"
        for suffix in SOURCE_FILES:
            filename = f"{prefix}_{suffix}"
            relative_source_path = f"bidmc_csv/{filename}"
            expected_hash = checksums.get(relative_source_path)
            if expected_hash is None:
                raise ValueError(f"No official SHA-256 checksum for {relative_source_path}")

            destination = OUT / filename
            if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected_hash:
                download(BASE_URL + relative_source_path, destination)

            actual_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"Checksum mismatch for {filename}: {actual_hash}")

        numerics_path = OUT / f"{prefix}_Numerics.csv"
        headers = list(pd.read_csv(numerics_path, nrows=0).columns)
        normalized = {str(header).strip().casefold(): str(header) for header in headers}
        mapped: dict[str, str] = {}
        for canonical, expected_header in EXPECTED_HEADERS.items():
            actual = normalized.get(expected_header)
            if actual is None:
                raise ValueError(
                    f"{numerics_path.name} is missing expected {canonical} header "
                    f"{expected_header!r}; found {headers}"
                )
            mapped[canonical] = actual
        header_mappings[patient_id] = {
            "recording_id": f"bidmc{recording}",
            "numerics_filename": numerics_path.name,
            "headers_in_file": headers,
            "mapped_columns": mapped,
        }

    (OUT / "header_mapping.json").write_text(
        json.dumps(header_mappings, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Downloaded and checksum-verified {len(RECORDINGS)} distinct BIDMC recordings to {OUT}")
    for patient_id, mapping in header_mappings.items():
        print(
            f"{patient_id}: {mapping['recording_id']} "
            f"{mapping['numerics_filename']} -> {mapping['mapped_columns']}"
        )


if __name__ == "__main__":
    main()
