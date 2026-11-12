from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the complete PS-03 BIDMC + Synthea five-patient pipeline."
    )
    parser.add_argument("--java", help="Path to Java 17+ executable or JDK directory.")
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--population-size", type=int, default=200)
    parser.add_argument("--max-population", type=int, default=800)
    parser.add_argument("--age-range", default="50-90")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    commands = [
        [sys.executable, str(SCRIPTS / "download_bidmc.py")],
        [
            sys.executable,
            str(SCRIPTS / "generate_synthea.py"),
            "--seed",
            str(args.seed),
            "--population-size",
            str(args.population_size),
            "--max-population",
            str(args.max_population),
            "--age-range",
            args.age_range,
        ],
        [sys.executable, str(SCRIPTS / "build_five_patients.py")],
        [sys.executable, str(SCRIPTS / "validate_dataset.py")],
    ]
    if args.java:
        commands[1].extend(["--java", args.java])
    if args.force:
        commands[1].append("--force")

    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
