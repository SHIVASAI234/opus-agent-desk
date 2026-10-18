"""Write a self-contained HTML dashboard for one pipeline run (plus backtest, if present)."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from .config import ROOT
from .schema import AGENT_ORDER

DECISION_CLASS = {"APPROVE_FOR_PAPER_TEST": "ok", "WAIT": "wait", "REJECT": "bad"}
STATUS_CLASS = {"complete": "ok", "needs_data": "wait", "rejected": "bad"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5b6170;--line:#e3e6ec;
--ok:#1a7f4b;--okbg:#e6f4ec;--wait:#8a5a00;--waitbg:#fdf3dc;--bad:#b42318;--badbg:#fdeceb;}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--ink:#e8eaef;--muted:#9aa1b0;
--line:#2a2e38;--ok:#5fd39a;--okbg:#14301f;--wait:#f0c060;--waitbg:#33290f;--bad:#ff8a80;--badbg:#3a1614;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0}.muted{color:var(--muted);font-size:13px}
.banner{border-radius:10px;padding:16px 18px;margin:18px 0;font-weight:600}
.ok{color:var(--ok);background:var(--okbg)}.wait{color:var(--wait);background:var(--waitbg)}
.bad{color:var(--bad);background:var(--badbg)}.skip{color:var(--muted);background:var(--bg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.pill{font-size:12px;font-weight:600;padding:2px 10px;border-radius:99px;text-transform:uppercase;letter-spacing:.03em}
ul{margin:8px 0 0;padding-left:18px}li{margin:3px 0}.label{font-size:12px;font-weight:600;color:var(--muted);
margin-top:10px;text-transform:uppercase;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;font-size:14px}td{padding:6px 4px;border-bottom:1px solid var(--line)}
td:first-child{color:var(--muted)}td:last-child{text-align:right;font-variant-numeric:tabular-nums}
"""


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(str(x))}</li>" for x in items) + "</ul>" if items else ""


def _agent_card(name: str, rep: dict | None) -> str:
    if rep is None:
        return (f'<div class="card"><div class="head"><h2>{name.upper()}</h2>'
                f'<span class="pill skip">skipped</span></div>'
                f'<div class="muted">Not run: an earlier agent stopped the pipeline.</div></div>')
    cls = STATUS_CLASS.get(rep["status"], "skip")
    ev = [f"{e['claim']}  (source: {e['source']})" for e in rep["evidence"]]
    parts = [f'<div class="card"><div class="head"><h2>{name.upper()}</h2>'
             f'<span class="pill {cls}">{escape(rep["status"])}</span></div>',
             f'<div class="muted">Generated {escape(rep["timestamp_utc"])} · data as of {escape(rep["data_as_of"])}</div>',
             f'<p>{escape(rep["summary"])}</p>']
    if ev:
        parts.append('<div class="label">Evidence</div>' + _list(ev))
    if rep["risks"]:
        parts.append('<div class="label">Risks / flags</div>' + _list(rep["risks"]))
    if rep["next_step"]:
        parts.append(f'<div class="label">Next step</div><div>{escape(rep["next_step"])}</div>')
    parts.append("</div>")
    return "".join(parts)


def _backtest_card(summary: dict[str, Any]) -> str:
    keys = ["period", "total_return_pct", "benchmark_equal_weight_buy_hold_pct", "max_drawdown_pct",
            "trades", "win_rate_pct", "avg_r_multiple", "approval_rate_pct", "costs"]
    rows = "".join(f"<tr><td>{escape(k.replace('_', ' '))}</td><td>{escape(str(summary.get(k)))}</td></tr>"
                   for k in keys)
    warn = _list(summary.get("warnings", []))
    return (f'<div class="card"><div class="head"><h2>Backtest</h2>'
            f'<span class="muted">{escape(summary.get("data_label", ""))}</span></div>'
            f'<table>{rows}</table>{"<div class=label>Warnings</div>" + warn if warn else ""}</div>')


def write_dashboard(result: dict[str, Any], out_path: Path | None = None) -> Path:
    out_path = out_path or ROOT / "reports" / "dashboard.html"
    decision = result["decision"]
    cards = "".join(_agent_card(n, result["reports"].get(n)) for n in AGENT_ORDER)
    bt_path = out_path.parent / "backtest_summary.json"
    bt = _backtest_card(json.loads(bt_path.read_text())) if bt_path.exists() else ""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Opus Agent Desk</title>
<style>{CSS}</style></head><body><main>
<h1>Opus Agent Desk</h1>
<div class="muted">Paper mode only · live orders disabled · {escape(result['data_label'])} ·
run {escape(result['run_timestamp_utc'])} · data as of {escape(result['data_as_of'])}</div>
<div class="banner {DECISION_CLASS.get(decision, 'skip')}">Final decision: {escape(decision)}
{' · human sign-off required' if decision == 'APPROVE_FOR_PAPER_TEST' else ''}</div>
{cards}{bt}
<p class="muted">Educational paper-trading research tool. Not financial advice. No orders are ever sent.</p>
</main></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    return out_path
