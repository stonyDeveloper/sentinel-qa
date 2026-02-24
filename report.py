import base64
import html
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def _data_uri(report: dict, name: str) -> str | None:
    data = report.get("shot_data", {}).get(name)
    if not data:
        return None
    encoded = base64.b64encode(data).decode()
    return f"data:image/png;base64,{encoded}"


def _fmt_result(result: str) -> str:
    return (result or "")[:300]


def build_markdown(report: dict) -> str:
    lines = [
        "# QA Agent Report",
        "",
        f"- **Application:** `{report['url']}`",
        f"- **Task:** {report['goal']}",
        f"- **Model:** {report['model']}",
        f"- **Run:** {report['timestamp']}",
        "",
        "## Actions Taken",
        "",
    ]
    for i, action in enumerate(report["actions"], 1):
        args = ", ".join(f"{k}={v}" for k, v in action["args"].items())
        lines.append(f"### {i}. `{action['name']}({args})`")
        result = _fmt_result(action["result"])
        if result:
            lines.extend(["```", result, "```"])
        lines.append("")
    if report["screenshots"]:
        lines.append("## Screenshots")
        lines.append("")
        for shot in report["screenshots"]:
            lines.append(f"- `{shot}`")
            lines.append("")
    lines.append("## Agent Final Report")
    lines.append("")
    lines.append(report["final_report"])
    lines.append("")
    return "\n".join(lines)


def build_html(report: dict) -> str:
    actions_html = []
    for i, action in enumerate(report["actions"], 1):
        args = ", ".join(f"{k}={v}" for k, v in action["args"].items())
        result = html.escape(_fmt_result(action["result"]))
        actions_html.append(
            f"<li><code>{html.escape(action['name'])} ({html.escape(args)})</code>"
            f"<pre>{result}</pre></li>"
        )
    shots_html = []
    for shot in report["screenshots"]:
        uri = _data_uri(report, shot)
        if uri:
            shots_html.append(
                f'<figure><img src="{uri}" alt="{html.escape(Path(shot).stem)}" />'
                f"<figcaption>{html.escape(Path(shot).name)}</figcaption></figure>"
            )
    body = "".join(
        f"<h2>Actions Taken</h2><ol>{''.join(actions_html)}</ol>"
        + (f"<h2>Screenshots</h2>{''.join(shots_html)}" if shots_html else "")
        + f"<h2>Agent Final Report</h2><div class='verdict'>{html.escape(report['final_report'])}</div>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>QA Agent Report</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto; max-width: 880px; padding: 0 1rem; color: #1f2937; }}
  h1 {{ font-size: 1.6rem; border-bottom: 2px solid #e5e7eb; padding-bottom: .5rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2rem; color: #111827; }}
  .meta td {{ padding: .2rem .8rem .2rem 0; }}
  ol {{ padding-left: 1.4rem; }}
  li {{ margin-bottom: .8rem; font-size: .9rem; }}
  pre {{ background: #f3f4f6; border-radius: 6px; padding: .6rem .8rem; white-space: pre-wrap; word-break: break-word; font-size: .78rem; }}
  figure {{ margin: .8rem 0; }}
  img {{ max-width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; }}
  figcaption {{ font-size: .8rem; color: #6b7280; margin-top: .3rem; }}
  code {{ background: #f3f4f6; border-radius: 4px; padding: .05rem .3rem; font-size: .85em; }}
  .verdict {{ white-space: pre-wrap; line-height: 1.5; background: #fff7ed; border: 1px solid #fdba74; border-radius: 8px; padding: 1rem; }}
</style>
</head>
<body>
  <h1>QA Agent Report</h1>
  <table class="meta">
    <tr><td><strong>Application</strong></td><td>{html.escape(report['url'])}</td></tr>
    <tr><td><strong>Task</strong></td><td>{html.escape(report['goal'])}</td></tr>
    <tr><td><strong>Model</strong></td><td>{html.escape(report['model'])}</td></tr>
    <tr><td><strong>Run</strong></td><td>{html.escape(report['timestamp'])}</td></tr>
  </table>
  {body}
</body>
</html>"""


def html_to_pdf(html_file: Path, pdf_file: Path) -> None:
    with sync_playwright() as pw:
        pdf_browser = pw.chromium.launch(headless=True)
        pdf_page = pdf_browser.new_page()
        pdf_page.goto(Path(html_file).resolve().as_uri())
        pdf_page.wait_for_timeout(300)
        pdf_page.pdf(
            path=str(pdf_file),
            format="A4",
            margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"},
            print_background=True,
        )
        pdf_browser.close()


def generate_report(report: dict, fmt: str, out_dir: Path) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = Path(report["timestamp"].replace(":", "-").replace(" ", "_"))
    created = []

    if fmt in ("md", "all"):
        target = out_dir / f"qa-report-{stamp}.md"
        target.write_text(build_markdown(report), encoding="utf-8")
        created.append(str(target))

    if fmt in ("html", "pdf", "all"):
        target = out_dir / f"qa-report-{stamp}.html"
        target.write_text(build_html(report), encoding="utf-8")
        created.append(str(target))

    if fmt in ("pdf", "all"):
        pdf_target = out_dir / f"qa-report-{stamp}.pdf"
        html_to_pdf(out_dir / f"qa-report-{stamp}.html", pdf_target)
        created.append(str(pdf_target))

    return created