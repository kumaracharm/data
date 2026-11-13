from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthguard.server import create_healthguard_server


def main() -> None:
    parser = argparse.ArgumentParser(description="HealthGuard PS-03 Clinical Intelligence Platform")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    print(f"============================================================")
    print(f"  HEALTHGUARD PS-03 CLINICAL INTELLIGENCE PLATFORM")
    print(f"  Role-Based Healthcare + Smart Wearable Digital Twin")
    print(f"============================================================")
    print(f"  * Local Web App: http://{args.host}:{args.port}")
    print(f"  * Roles: Patient, PHEON, Nurse, Doctor, Senior Doctor")
    print(f"  * Press Ctrl+C to terminate server")
    print(f"============================================================")

    server = create_healthguard_server(ROOT, host=args.host, port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down HealthGuard server...")
        server.server_close()


if __name__ == "__main__":
    main()
