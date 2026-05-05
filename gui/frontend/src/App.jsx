import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";

const API = "/api";

function fetchJson(path, opts) {
  return fetch(path, opts).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
    return data;
  });
}

export default function App() {
  const [model, setModel] = useState(null);
  const [url, setUrl] = useState("https://demo.playwright.dev/todomvc");
  const [goal, setGoal] = useState(
`Verify that when a user filters tasks using the "Active" filter, only incomplete tasks are shown.`
  );
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | running | done | failed
  const [log, setLog] = useState([]);
  const [logOffset, setLogOffset] = useState(0);
  const [shots, setShots] = useState([]);
  const [reports, setReports] = useState([]);
  const [reportMd, setReportMd] = useState(null);
  const [tab, setTab] = useState("activity");
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [viewShot, setViewShot] = useState(null);

  const lastLogRef = useRef(null);
  const pollRef = useRef(null);
  const t0Ref = useRef(null);

  const running = status === "running";

  useEffect(() => {
    fetchJson(`${API}/health`)
      .then((h) => setModel(h.model))
      .catch(() => setModel("unknown"));
  }, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  useEffect(() => {
    if (!running) return;
    t0Ref.current = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - t0Ref.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [running]);

  function fmt(sec) {
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function verdict() {
    const all = [...log, reportMd || ""].join("\n");
    if (status !== "done") return null;
    if (!all.trim()) return null;
    if (/(reached \d+-action limit|halted mid-run|no final verdict)/i.test(all))
      return { label: "Inconclusive", cls: "amber" };
    if (/(bugs? found)?:?\s*-?\s*none|no bugs? found|passed checks/i.test(all) && !/(bugs found|fail)/i.test(all))
      return { label: "Pass", cls: "emerald" };
    if (/(bugs? found:\s*-|fails|failed|error)/i.test(all))
      return { label: "Bugs found", cls: "rose" };
    return { label: "Inconclusive", cls: "amber" };
  }

  async function start() {
    setError(null);
    setLog([]);
    setLogOffset(0);
    setShots([]);
    setReports([]);
    setReportMd(null);
    setStatus("running");
    try {
      const { job_id } = await fetchJson(`${API}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, goal }),
      });
      setJobId(job_id);
      setTab("activity");
      pollRef.current = setInterval(() => poll(job_id), 1200);
    } catch (e) {
      setStatus("idle");
      setError(e.message);
    }
  }

  async function poll(id) {
    try {
      const s = await fetchJson(`${API}/jobs/${id}`);
      if (s.log_size > logOffset) {
        const chunk = await fetchJson(`${API}/jobs/${id}/log?offset=${logOffset}`);
        setLogOffset(chunk.offset);
        setLog((prev) => [...prev, ...chunk.lines]);
      }
      setShots(s.screenshots);
      setReports(s.reports);
      if (s.status === "done" || s.status === "failed") {
        clearInterval(pollRef.current);
        setStatus(s.status);
        if (s.reports.some((r) => r.format === "md")) {
          const md = await fetch(`${API}/jobs/${id}/report?fmt=md`).then((r) => r.text());
          setReportMd(md);
        }
        if (s.log_size > logOffset) {
          const chunk = await fetchJson(`${API}/jobs/${id}/log?offset=${logOffset}`);
          setLogOffset(chunk.offset);
          setLog((prev) => [...prev, ...chunk.lines]);
        }
      }
    } catch (e) {
      clearInterval(pollRef.current);
      setStatus("idle");
      setError(e.message);
    }
  }

  useEffect(() => {
    const el = lastLogRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  return (
    <div className="bg-scene min-h-screen text-slate-100">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <header className="mb-8 flex items-center justify-between">
          <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-cyan-400 text-lg shadow-lg shadow-violet-500/30">
              ◆
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight gradient-text">Sentinel QA</h1>
              <p className="text-xs text-slate-400">Agentic test console</p>
            </div>
          </motion.div>
          <div className="flex items-center gap-3 text-xs text-slate-400">
            {verdict() && (
              <span
                className={`rounded-full border px-3 py-1 font-semibold ${
                  verdict().cls === "emerald"
                    ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                    : verdict().cls === "rose"
                      ? "border-rose-400/40 bg-rose-400/10 text-rose-300"
                      : "border-amber-400/40 bg-amber-400/10 text-amber-300"
                }`}
              >
                {verdict().label}
              </span>
            )}
            <span className={`flex items-center gap-2 ${running ? "" : "hidden"}`}>
              <span className="pulse-dot h-2 w-2 rounded-full bg-cyan-400" />
              agent running • {fmt(elapsed)}
            </span>
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 font-mono">{model || "…"}</span>
          </div>
        </header>

        {error && (
          <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            {error}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
          <motion.section
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            className="glass rounded-2xl p-6"
          >
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
              New acceptance test
            </h2>
            <label className="mb-1.5 block text-xs text-slate-400">Target URL</label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={running}
              spellCheck={false}
              placeholder="https://…"
              className="mb-4 w-full rounded-xl border border-white/10 bg-black/30 px-4 py-3 font-mono text-sm outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20"
            />

            <label className="mb-1.5 block text-xs text-slate-400">
              Acceptance criterion — one clean sentence
            </label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              disabled={running}
              rows={6}
              spellCheck={false}
              placeholder="e.g. When I add two items and complete one, the active filter shows only the incomplete task."
              className="mb-5 w-full resize-none rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20"
            />

            <button
              onClick={start}
              disabled={running || !url.trim() || !goal.trim()}
              className="glow-btn w-full rounded-xl py-3.5 text-sm font-semibold text-white"
            >
              {running ? "Testing in progress…" : jobId ? "Run again" : "Run test"}
            </button>

            {jobId && (
              <div className="mt-4 rounded-xl border border-white/10 bg-black/20 px-4 py-3">
                <p className="text-xs text-slate-400">Job</p>
                <p className="font-mono text-sm text-cyan-300">{jobId}</p>
              </div>
            )}
          </motion.section>

          <motion.section
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            className="glass min-w-0 rounded-2xl p-6"
          >
            <div className="mb-4 flex items-center gap-2">
              {[
                ["activity", "Activity"],
                ["evidence", "Evidence"],
                ["report", "Report"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setTab(key)}
                  className={`rounded-lg px-4 py-1.5 text-sm transition ${
                    tab === key
                      ? "bg-white/10 text-white"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {label}
                  {key === "evidence" && shots.length > 0 && (
                    <span className="ml-1 text-cyan-400">{shots.length}</span>
                  )}
                </button>
              ))}
              <div className="ml-auto flex gap-2">
                {reports.length > 0 ? (
                  reports.map((r) => (
                    <a
                      key={r.format}
                      href={`${API}/jobs/${jobId}/report?fmt=${r.format}`}
                      download={`qa-report-${jobId}.${r.format}`}
                      className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-white/10 hover:text-white"
                    >
                      ⤓ {r.format.toUpperCase()}
                    </a>
                  ))
                ) : (
                  <span className="text-xs text-slate-600">reports appear when done</span>
                )}
              </div>
            </div>

            <AnimatePresence mode="wait">
              {tab === "activity" && (
                <motion.div key="activity" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <div ref={lastLogRef} className="scroll-slim h-[560px] overflow-auto rounded-xl border border-white/10 bg-black/40 p-4 font-mono text-[13px] leading-relaxed">
                    {log.length === 0 && !running && (
                      <p className="text-slate-600">Agent activity will appear here.</p>
                    )}
                    {log.length === 0 && running && (
                      <p className="text-slate-500">
                        <span className="pulse-dot inline-block h-2 w-2 rounded-full bg-cyan-400" /> initializing browser…
                      </p>
                    )}
                    {log.map((line, i) => {
                      const isTool = /^\[/.test(line) || /^\]/.test(line);
                      const isReport = line.includes("FINAL REPORT");
                      return (
                        <div
                          key={i}
                          className={
                            isTool
                              ? "text-emerald-300/90"
                              : isReport
                                ? "mt-3 text-lg font-bold text-cyan-300"
                                : line.trim()
                                  ? "text-slate-200"
                                  : "text-slate-200"
                          }
                        >
                          {isTool ? line : <span className="opacity-60">› </span>}
                          {line}
                        </div>
                      );
                    })}
                  </div>
                </motion.div>
              )}

              {tab === "evidence" && (
                <motion.div key="evidence" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  {shots.length === 0 ? (
                    <div className="grid h-[560px] place-items-center rounded-xl border border-dashed border-white/10 text-slate-600">
                      Screenshots stream in as the agent captures them.
                    </div>
                  ) : (
                    <div className="scroll-slim h-[560px] overflow-auto">
                      <div className="columns-1 gap-3 sm:columns-2">
                        {shots.map((name) => (
<figure
                          key={name}
                          className="mb-3 break-inside-avoid overflow-hidden rounded-xl border border-white/10 transition hover:border-violet-400/40"
                        >
                          <img
                            src={`${API}/jobs/${jobId}/screenshot/${encodeURIComponent(name)}`}
                            alt={name}
                            onClick={() => setViewShot(name)}
                            className="w-full cursor-zoom-in"
                          />
                          <figcaption className="border-t border-white/10 bg-black/30 px-3 py-1.5 font-mono text-xs text-slate-400">
                            {name}
                          </figcaption>
                        </figure>
                        ))}
                      </div>
                    </div>
                  )}
                </motion.div>
              )}

              {tab === "report" && (
                <motion.div key="report" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  {reportMd && reports.length > 0 && (
                    <div className="mb-4 flex flex-wrap items-center gap-2">
                      <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">
                        Download report
                      </span>
                      {reports.map((r) => (
                        <a
                          key={r.format}
                          href={`${API}/jobs/${jobId}/report?fmt=${r.format}`}
                          download={`qa-report-${jobId}.${r.format}`}
                          className="glow-btn rounded-lg px-4 py-2 text-xs font-semibold text-white"
                        >
                          ⤓ {r.format.toUpperCase()}
                        </a>
                      ))}
                    </div>
                  )}
                  <div className="scroll-slim h-[520px] overflow-auto whitespace-pre-wrap rounded-xl border border-white/10 bg-black/40 p-5 font-mono text-[13px] leading-relaxed text-slate-200">
                    {reportMd ? (
                      reportMd
                    ) : status === "done" ? (
                      <span className="text-slate-500">No markdown report generated.</span>
                    ) : status === "failed" ? (
                      <span className="text-rose-300">The run failed — see the Activity tab for the error.</span>
                    ) : (
                      <span className="text-slate-600">The report appears when the run finishes.</span>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.section>
        </div>
      </div>

      <AnimatePresence>
        {viewShot && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setViewShot(null)}
            className="fixed inset-0 z-50 grid cursor-zoom-out place-items-center bg-black/80 p-8 backdrop-blur-sm"
          >
            <img
              src={`${API}/jobs/${jobId}/screenshot/${encodeURIComponent(viewShot)}`}
              alt={viewShot}
              className="max-h-full max-w-full rounded-xl border border-white/10 shadow-2xl"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}