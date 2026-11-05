from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    project = root / "projects" / "customer-service-agent"
    commands = [
        (root, [sys.executable, "scripts/check_public_content.py"]),
        (project, [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        (project, [sys.executable, "demo.py"]),
    ]
    for directory, command in commands:
        subprocess.run(command, cwd=directory, check=True)
    print("All portfolio checks passed.")


if __name__ == "__main__":
    main()
