"""Run directly in VSCode to check R, dependencies, and one real GIFT solve."""
import json
from r_engine import RBackendError, avg_hydraulics, runtime_info


def main():
    try:
        info = runtime_info()
        frame = avg_hydraulics(.002, 10., .5, 60., full_curve=True)
    except RBackendError as exc:
        print(f"R GIFT setup failed: {exc}")
        return 1
    print(json.dumps(info, indent=2))
    print(f"Original R GIFT calculated {len(frame)} native stages successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
