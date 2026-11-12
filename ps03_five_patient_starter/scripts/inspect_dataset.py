from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_dataset.py"


if __name__ == "__main__":
    subprocess.run([sys.executable, str(VALIDATOR)], cwd=ROOT, check=True)
