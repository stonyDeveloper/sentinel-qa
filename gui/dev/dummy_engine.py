import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", required=True)
parser.add_argument("--format", default=["md"], action="append", choices=["md", "html", "pdf", "all"])
args = parser.parse_args()

report_dir = Path(os.getenv("QA_REPORT_DIR", "reports"))
stream = os.getenv("QA_SHOTS_STREAM") or ""
report_dir.mkdir(parents=True, exist_ok=True)

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def shot(name):
    if stream:
        p = Path(stream)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "png": PNG_B64}) + "\n")
    print(f"Screenshot saved: {name}")


print(">>> Testing", args.url)
print(">>> Goal:", args.goal)
print(">>> Model: dummy-engine (no tokens)")
print("[navigate {'url': '...'}]")
time.sleep(0.4)
print("[read_page {}]")
time.sleep(0.4)
print("Navigated. Page title: 'Dummy App'")
time.sleep(0.4)
print("[click {'text': 'Submit'}]")
shot("evidence_dummy.png")
time.sleep(0.4)
print("=== FINAL REPORT ===")
print("Passed checks: the acceptance criterion holds.")
print("Bugs found: none.")

md = f"""# QA Agent Report

- **Application:** `{args.url}`
- **Task:** {args.goal}
- **Model:** dummy-engine

## Agent Final Report

Passed checks: the acceptance criterion holds.

## Screenshots

- `evidence_dummy.png`
"""
report_dir.joinpath("qa-report-DUMMY.md").write_text(md, encoding="utf-8")
report_dir.joinpath("qa-report-DUMMY.html").write_text(
    f"<h1>QA Agent Report</h1><p>Task: {args.goal}</p>", encoding="utf-8"
)
print("Report written:", report_dir / "qa-report-DUMMY.md")
sys.exit(0)