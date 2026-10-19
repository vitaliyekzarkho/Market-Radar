from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


PROJECT_DIRECTORY = Path(__file__).resolve().parent

STEPS = [
    ("SAVE SNAPSHOT", "save_snapshot.py"),
    ("HEALTH CHECK", "health_check.py"),
]


def run_step(step_name, script_name):
    script_path = PROJECT_DIRECTORY / script_name

    print(f"\n===== {step_name} =====")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=PROJECT_DIRECTORY,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{step_name} failed with "
            f"exit code {result.returncode}"
        )

    print(f"{step_name}: COMPLETED")


def main():
    started_at = datetime.now(timezone.utc)

    print("MARKET RADAR CYCLE STARTED")
    print(f"STARTED UTC: {started_at}")

    for step_name, script_name in STEPS:
        run_step(step_name, script_name)

    finished_at = datetime.now(timezone.utc)

    print("\nMARKET RADAR CYCLE: PASSED")
    print(f"FINISHED UTC: {finished_at}")
    print(
        "DURATION_SECONDS: "
        f"{(finished_at - started_at).total_seconds():.2f}"
    )


if __name__ == "__main__":
    main()