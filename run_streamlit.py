"""Select the gift_streamlit environment, then run this file in VSCode."""
from pathlib import Path
import subprocess
import sys
from r_engine import RBackendError, close_worker, runtime_info

ROOT = Path(__file__).resolve().parent
PORT = 8501  # Change if another local app already uses this port.


def main():
    try:
        info = runtime_info()
        close_worker()
    except RBackendError as exc:
        raise SystemExit(f"R GIFT setup failed: {exc}\nRun setup_r.R in R, then check_r.py in VSCode.") from exc
    print(f"Calculation engine: Original R GIFT; {info['R_version']}; revision {info['revision'][:12]}")
    command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
               f"--server.port={PORT}", "--server.address=127.0.0.1", "--server.headless=false"]
    print(f"Opening GIFT Habitat Explorer at http://localhost:{PORT}")
    print("Stop this terminal with Ctrl+C to close the local server.")
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
