"""Check the portable overview, then scientific and on-demand behavior in VSCode."""
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    checks = (
        ("Checking bundled data before app startup...", ["tests/test_portable_deployment.py"]),
        ("Running scientific, data-preparation, and app-state checks...",
         ["tests", "--ignore=tests/test_portable_deployment.py"]),
    )
    for message, targets in checks:
        print(message, flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", *targets, "-q"],
                                cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
