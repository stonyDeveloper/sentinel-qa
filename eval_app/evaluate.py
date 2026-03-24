import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # eval_app/
PROJECT = ROOT.parent                             # qa-agent/
SITE = ROOT / "site"
REPORTS = PROJECT / "reports"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

MAX_STEPS = 70


def wait_for_server(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def build_goal(checks):
    lines = []
    for c in checks:
        lines.append(f"{c['id']}: {c['check']}")
    return (
        "Perform an acceptance test on this bookstore app. Before acting, write out a short "
        "plan covering ALL checks below. Then execute each check exactly once; start each "
        "scenario with a fresh navigate() to reset state, and do NOT repeat a check that "
        "already succeeded. Only re-run something if the first attempt errored.\n\n"
        "- The 'Add to cart' buttons, the quantity '-'/'+' buttons, and each row's 'Remove' "
        "button all carry aria-labels with the book title. Use click_selector with e.g. "
        "'[aria-label=\"Increase The Cat in the Hat\"]' to target a specific row's button.\n"
        "- Quantity buttons: add a book, click '+' twice, then read the qty and Subtotal.\n"
        "- Floor: from qty 1 click '-' and see if qty can drop to 0.\n"
        "- Email: open Checkout, fill the form, submit with an invalid email like 'abc'.\n"
        "- Remove: add two different books, remove the SECOND one, then read which row "
        "remains.\n"
        "- Search: type a nonsense term, then read the product list count.\n"
        "- After a successful checkout the app shows an 'Order placed' panel; close it via "
        "its Close button, not Escape.\n"
        "Take a screenshot whenever something looks broken.\n\n"
        "When all checks are done, output the verdict as exactly one line per check in the "
        "form 'ID: PASS' if the behavior works or 'ID: FAIL' if it is broken, followed by a "
        "one-line evidence sentence per verdict.\n\n"
        "Checks to answer:\n" + "\n".join(lines)
    )


def parse_verdicts(text, checks):
    ids = [c["id"] for c in checks]
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(i) for i in ids) + r")\s*[:=.,]\s*(PASS|FAIL|WARN|N/A|NO)\b",
        re.IGNORECASE | re.MULTILINE,
    )
    verdicts = {}
    for m in pattern.finditer(text):
        verdicts[m.group(1).lower()] = m.group(2).upper()
    return verdicts


def score(verdicts, checks):
    rows = []
    tp = fp = tn = fn = 0
    for c in checks:
        expected_bug = c["bug_present"]
        ans = verdicts.get(c["id"])
        if ans is None:
            status = "MISSING"
        elif ans in ("PASS", "WARN"):
            status = "PASS-WORKS"
        elif ans == "FAIL":
            status = "FAIL-BROKEN"
        else:
            status = "MISSING"

        if expected_bug:
            if ans == "FAIL":
                tp += 1
            else:
                fn += 1
        else:
            if ans == "FAIL":
                fp += 1
            elif ans in ("PASS", "WARN"):
                tn += 1
        rows.append((c["id"], expected_bug, ans, status))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / len(checks)
    return rows, {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
                  "precision": round(precision, 3),
                  "recall": round(recall, 3),
                  "f1": round(f1, 3),
                  "accuracy": round(accuracy, 3)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Grade the QA agent on seeded bugs")
    parser.add_argument("--mock", action="store_true",
                        help="score a canned report without calling the agent")
    args = parser.parse_args()

    checks = json.loads((ROOT / "checks.json").read_text(encoding="utf-8"))
    goal = build_goal(checks)
    reports_dir = REPORTS
    report_name = "MOCK-2026.md"

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1",
         "--directory", str(SITE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_server():
            raise RuntimeError(f"Server on {BASE} did not come up.")
        print(f"Seeded app served at {BASE}\n")

        started = time.time()
        env = dict(os.environ)
        env["QA_PACING"] = "3"
        env["QA_MAX_STEPS"] = str(MAX_STEPS)
        if args.mock:
            fake = ("qty_total: FAIL\n"
                    "qty_floor: FAIL\n"
                    "email_valid: PASS\n"
                    "remove_item: FAIL\n"
                    "search_filter: FAIL\n"
                    "cart_add: PASS\n"
                    "stock_badge: PASS\n")
            reports_dir.mkdir(exist_ok=True)
            mock_path = reports_dir / "qa-report-MOCK-2026.md"
            mock_path.write_text(fake, encoding="utf-8")
            print(f"[mock] wrote {mock_path} and skipped the agent call")
            report = fake
            report_name = mock_path.name
            elapsed = 0
            proc = None
        else:
            proc = subprocess.run(
                [sys.executable, str(PROJECT / "qa_agent.py"),
                 "--url", BASE,
                 "--goal", goal,
                 "--format", "md"],
                cwd=str(PROJECT),
                env=env,
                timeout=60 * 30,
            )
            elapsed = time.time() - started
            if proc.returncode != 0:
                print("Agent exited with an error; grading whatever report exists.")

            reports = sorted(
                (p for p in reports_dir.glob("qa-report-*.md")
                 if p.stat().st_mtime >= started - 5),
                key=lambda p: p.stat().st_mtime,
            )
            if not reports:
                raise RuntimeError("No report produced during this run.")
            report = reports[-1].read_text(encoding="utf-8")
            report_name = reports[-1].name

        verdicts = parse_verdicts(report, checks)
        rows, metrics = score(verdicts, checks)

        env_model = next(
            (l.split("=", 1)[1].strip() for l in (PROJECT / ".env").read_text(encoding="utf-8").splitlines()
             if l.startswith("OPENAI_MODEL=")),
            "?",
        )

        print("\n=== EVAL RESULT ===")
        print(f"model: {env_model}")
        print(f"steps allowed: {MAX_STEPS} | run time: {elapsed:.0f}s")
        print(f"report: {report_name}\n")
        print(f"{'check':<14}{'buggy?':<8}{'agent':<10}{'status'}")
        for cid, buggy, ans, status in rows:
            print(f"{cid:<14}{str(buggy):<8}{str(ans):<10}{status}")
        print()
        for k, v in metrics.items():
            print(f"{k}: {v}")

        result = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": env_model,
            "seconds": round(elapsed),
            "goal": goal,
            "rows": [{"check": cid, "buggy": buggy, "agent": ans, "status": status}
                     for cid, buggy, ans, status in rows],
            "metrics": metrics,
        }
        out = ROOT / f"eval-results-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nSaved: {out}")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()