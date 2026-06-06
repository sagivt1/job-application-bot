"""job-application-bot: automated job-posting intake, scoring, and alerting."""

from pathlib import Path

# Repo root, resolved relative to this file
# (src/job_application_bot/__init__.py -> two up).
# Used to locate user data that lives outside the package — notably ``cv.md``,
# which is gitignored and kept at the repo root rather than shipped in the wheel.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
