import base64
import json
import os
import subprocess
import sys
import uuid
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent          # qa-agent/
GUI_DIR = Path(__file__).resolve().parent               # qa-agent/gui/
JOBS_DIR = Path(os.getenv("QA_JOBS_DIR", GUI_DIR / "jobs"))
JOBS_DIR.mkdir(exist_ok=True)
for _d in JOBS_DIR.iterdir():                           # drop transport leftovers from crashes
    if _d.is_dir():
        (_d / "shots.jsonl").unlink(missing_ok=True)

PY = ROOT / "venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
ENGINE = str(ROOT / "qa_agent.py")

app = FastAPI(title="QA Engine API", version="0.1.0")

jobs: dict[str, dict] = {}

def _client_model() -> str:
    m = os.getenv("OPENAI_MODEL")
    if m:
        return m
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_MODEL="):
                m = line.split("=", 1)[1].strip()
                if m:
                    return m
    except OSError:
        pass
    return "?"


CLIENT_MODEL = _client_model()


class RunRequest(BaseModel):
    url: str
    goal: str


def _safe_name(name: str) -> str:
    keep = name
    for ch in '\\/:*?"<>|':
        keep = keep.replace(ch, "_")
    return keep


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    if not d.exists():
        raise HTTPException(404, "job not found")
    return d


def _report_files(job_id: str) -> list[dict]:
    d = _job_dir(job_id)
    out = []
    for fmt in ("md", "html", "pdf"):
        hits = sorted(d.glob(f"report/qa-report-*.{fmt}"))
        if hits:
            out.append({"format": fmt, "name": hits[-1].name})
    return out


def _drain_shots(job_id: str) -> None:
    rec = jobs.get(job_id)
    if rec is None:
        return
    stream = rec["dir"] / "shots.jsonl"
    if not stream.exists():
        return
    off = rec.get("shots_off", 0)
    size = stream.stat().st_size
    if size <= off:
        return
    try:
        with open(stream, "r", encoding="utf-8", errors="replace") as f:
            f.seek(off)
            chunk = f.read()
    except OSError:
        return
    end = chunk.rfind("\n")
    if end == -1:
        return
    consumed = chunk[: end + 1]
    rec["shots_off"] = off + len(consumed.encode("utf-8"))
    for line in consumed.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            rec["shots"][obj["name"]] = "data:image/png;base64," + obj["png"]
        except Exception:
            pass


def _state(job_id: str) -> dict:
    _drain_shots(job_id)
    rec = jobs[job_id]
    d = rec["dir"]
    proc = rec.get("proc")
    if proc is None:
        status = "done"
    else:
        code = proc.poll()
        status = "done" if code == 0 else ("failed" if code is not None else "running")
    if status != "running":
        stream = d / "shots.jsonl"
        if stream.exists() and rec.get("shots_off", 0) >= stream.stat().st_size:
            try:
                stream.unlink()
            except OSError:
                pass
    return {
        "id": job_id,
        "url": rec["url"],
        "goal": rec["goal"],
        "status": status,
        "created": rec["created"],
        "screenshots": list(rec["shots"].keys()),
        "reports": _report_files(job_id),
        "log_size": (d / "run.log").stat().st_size if (d / "run.log").exists() else 0,
    }


@app.get("/api/health")
def health():
    return {"ok": True, "model": CLIENT_MODEL, "active_jobs": len(jobs)}


@app.post("/api/run")
def start_run(body: RunRequest):
    url = body.url.strip()
    goal = body.goal.strip()
    if not url or not goal:
        raise HTTPException(400, "both url and goal are required")
    for rec in jobs.values():
        proc = rec.get("proc")
        if proc is not None and proc.poll() is None:
            raise HTTPException(409, "a run is already in progress")

    job_id = uuid.uuid4().hex[:8]
    jdir = JOBS_DIR / job_id
    report_dir = jdir / "report"
    report_dir.mkdir(parents=True)

    log = open(jdir / "run.log", "w", encoding="utf-8", buffering=1)
    env = dict(os.environ)
    env["QA_REPORT_DIR"] = str(report_dir)
    env["QA_SHOTS_STREAM"] = str(jdir / "shots.jsonl")
    env["QA_PACING"] = os.getenv("QA_PACING", "3")
    env["QA_MAX_STEPS"] = os.getenv("QA_MAX_STEPS", "45")
    env["PYTHONUNBUFFERED"] = "1"

    engine = os.getenv("QA_ENGINE", ENGINE)
    args = [str(PY), engine, "--url", url, "--goal", goal, "--format", "md", "--format", "html"]
    proc = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    jobs[job_id] = {
        "proc": proc,
        "url": url,
        "goal": goal,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dir": jdir,
        "shots": {},
        "shots_off": 0,
    }
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    return _state(job_id)


@app.get("/api/jobs/{job_id}/log")
def tail_log(job_id: str, offset: int = 0):
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    path = _job_dir(job_id) / "run.log"
    size = path.stat().st_size if path.exists() else 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(max(0, offset))
        chunk = f.read(16384)
    return {"offset": offset + len(chunk), "size": size, "lines": chunk.splitlines()}


@app.get("/api/jobs/{job_id}/screenshot/{name}")
def screenshot(job_id: str, name: str):
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    rec = jobs[job_id]
    _drain_shots(job_id)
    uri = rec["shots"].get(_safe_name(Path(name).name))
    if not uri:
        raise HTTPException(404, "screenshot not found")
    return Response(content=base64.b64decode(uri.split(",", 1)[1]), media_type="image/png")


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str, fmt: str = "md"):
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    hits = sorted(_job_dir(job_id).glob(f"report/qa-report-*.{fmt}"))
    if not hits:
        raise HTTPException(404, f"no {fmt} report yet")
    text = hits[-1].read_text(encoding="utf-8")
    media = {"md": "text/markdown", "html": "text/html", "pdf": "application/pdf"}
    if fmt == "html":
        return PlainTextResponse(text, media_type="text/html")
    if fmt == "md":
        return PlainTextResponse(text, media_type="text/markdown")
    path = hits[-1]
    return FileResponse(path, media_type=media.get(fmt, "application/octet-stream"))


dist = GUI_DIR / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


def _prune(age_hours: float = 6):
    now = time.time()
    for name, rec in list(jobs.items()):
        proc = rec.get("proc")
        age = now - rec["dir"].stat().st_mtime
        if proc is None and age > age_hours * 3600:
            (rec["dir"] / "shots.jsonl").unlink(missing_ok=True)
            jobs.pop(name, None)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("QA_GUI_PORT") or "8000")
    uvicorn.run(app, host=host, port=port)