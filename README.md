# Opus Agent Desk

An auditable six-agent research pipeline for **paper trading only**, with hard-coded risk controls, a walk-forward backtest, and a decision log.

```
SCOUT → NEWS → CHARTS → STRATEGY → RISK → BOSS
```

> **Educational project. Not financial advice.** There is no brokerage integration and no order-sending code. The default data is synthetic and the tickers (`TEST_AAA` …) are fictional.

---

## Why it's built this way

This started from a viral "6-agent AI trading system" guide. The pipeline idea is sound; the weak point is letting language models do maths and enforce risk rules. So this version puts each job where it belongs:

| Agent | What it does | How | Why |
|---|---|---|---|
| **Scout** | Flags unusual volume, moves and breakouts | Python (z-scores) | Statistics, not language |
| **News** | Summarises headlines, flags stale/duplicate/unattributed items and scheduled events | Keyword rules by default, **Claude optional** | The one task that is genuinely about reading text |
| **Charts** | Trend, RSI, ATR, support/resistance, volume | Python (indicators) | An LLM can misread numbers; pandas can't |
| **Strategy** | One written-down rule → a plan or WAIT | Python | Rules you can backtest |
| **Risk** | Sizing, exposure, liquidity, correlation, events, freshness. **Veto power.** | Python | A control must be code, not a prompt that says "please don't" |
| **Boss** | Approves only if every report is valid, complete and Risk passed | Python | A gate that can't be talked round |

Every agent returns the same JSON envelope, validated before the next agent sees it. Evidence without a source fails validation.

---

## Run it in GitHub Codespaces (nothing to install locally)

1. On this repo's GitHub page: **Code → Codespaces → Create codespace on main**.
2. In the terminal:

```bash
pip install -r requirements.txt     # once per codespace
python main.py                      # run the six agents on the latest date
python main.py run --as-of 2026-09-17   # a date where the system approves a paper trade
python main.py run --as-of 2026-09-22   # a date where Risk vetoes (earnings in the holding window)
python main.py backtest             # walk-forward backtest over the full history
pytest                              # 32 tests
```

3. To see the dashboard, run `python -m http.server 8000`, click **Open in Browser** on the pop-up (or the **Ports** tab → port 8000), then go to `/reports/dashboard.html`. Press `Ctrl+C` in the terminal to stop the server.
4. When you're done: **Codespaces** (bottom-left) → **Stop Current Codespace**, so you don't use up your free hours.

Running locally instead works the same: create a virtual environment with Python 3.11+, then use the same commands.

---

## What you get after a run

| File | Contents |
|---|---|
| `reports/run_<date>_<timestamp>.json` | All six reports for one run |
| `reports/decision_log.csv` | Append-only audit log, one row per run |
| `reports/dashboard.html` | Visual view of the latest run (+ backtest if run) |
| `reports/backtest_summary.json` | Return, benchmark, drawdown, win rate, R-multiple, approval rate |
| `reports/backtest_trades.csv` | Every simulated trade with entry, exit, reason and P&L |
| `reports/backtest_equity.csv` | Daily equity curve |

---

## The backtest, and how to read it

- At each date the pipeline sees **only data up to that date** (tested in `test_no_lookahead`).
- Approved plans fill at the **next day's open**, with commission and slippage on both sides.
- If a bar touches both stop and target, the **stop is assumed first**. Gaps through the stop exit at the open.
- Results are compared with an **equal-weight buy-and-hold** benchmark.

On the bundled synthetic data the system makes about 16 trades and **badly trails buy-and-hold**. That's the point of including it: a pipeline that runs cleanly is not a strategy with an edge, and 16 trades proves nothing either way.

---

## Safety controls (all tested)

- `mode: paper` and `allow_live_orders: false` are checked at startup; anything else stops the program. Environment variables can't override them.
- No brokerage or order code exists (a test scans the package for it).
- An agent that crashes, returns invalid JSON or reports `needs_data` stops the pipeline, and Boss can then only return WAIT or REJECT.
- Risk rejects on: oversized position, too much total exposure, too many positions, already held, illiquid, too correlated with a holding, stale data, or a scheduled event inside the holding window.
- Every approval still says `human_approval_required: true`.

---

## Optional: use Claude for the News agent

```bash
pip install -r requirements-llm.txt
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
```

Set `news.use_llm: true` in `config.yaml`. Claude only classifies headlines (sentiment and fact/opinion/unverified) using `prompts/news.md`. Its reply is schema-checked, it must return exactly the items it was given, and an invalid reply gets one retry before the pipeline stops.

---

## Project layout

```
opus_desk/
  config.py        load config + enforce paper-only safety
  schema.py        shared report envelope + validator
  data.py          load fixtures, cut everything at the as-of date
  indicators.py    SMA, RSI, ATR, z-score
  agents/          scout, news, charts, strategy, risk, boss
  pipeline.py      runs the agents in order, saves reports + audit log
  backtest.py      walk-forward simulation with costs and benchmark
  dashboard.py     static HTML dashboard
  llm.py           optional Claude client (News only)
prompts/news.md    the only LLM prompt
data/fixtures/     synthetic TEST DATA (regenerate: python scripts/make_fixtures.py)
tests/             32 tests
config.yaml        every rule and limit in one place
```

## Ideas for next steps

1. Swap the synthetic fixtures for real historical daily data (keep the same CSV columns: `date,open,high,low,close,volume`).
2. Test across many more tickers and years until you have hundreds of trades.
3. Add a parameter-sensitivity report (does the result survive small changes to the rules?).
4. Keep a separate log for any forward paper tests. Never mix backtest, paper and live results.

## License

MIT
