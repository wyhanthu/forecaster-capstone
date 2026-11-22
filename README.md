# Forecaster: an agentic SEC-filing screen of obligors for ABS / CLO collateral

Given a **maturity date**, a **number of companies** and a **mode** (RegCap or CLO), Forecaster screens every listed
US company from the SEC's public XBRL bulk files, reads the filings of the best candidates with an LLM, and writes a
cited, diversified shortlist with a memo per company. It is a first-pass screen for analysts, not a credit rating and not
investment advice. The environment is read-only: nothing is traded and no account is touched.

Built for the Agentic AI capstone (Checkpoints 1-6) in the structure of the course's `research_agent` sample and labs
(MCP-style tool registry, LangGraph `StateGraph`, ReAct, Tree-of-Thought, MMR, guardrails, audit log, human review).

## Get it running (step by step)

Follow the steps in order. Steps 1 to 4 are one-time setup; step 5 is the run.

**Run every command in this guide from the project folder** (the `forecaster-capstone` folder that contains `README.md`, the
`forecaster/` package and `ratings.csv`). Commands such as `python -m forecaster.cli ...` and the relative paths `data/...`
and `ratings.csv` only work from there. Each time you open a new terminal, do:

```bash
cd forecaster-capstone                 # or wherever you cloned it
source .venv/bin/activate              # Windows: .venv\Scripts\activate
```

### 1. What you need

| Item | Notes |
|---|---|
| Python 3.10 or newer | check with `python3 --version` |
| git | to clone the repository |
| An LLM API key (recommended) | OpenRouter or OpenAI; any OpenAI-compatible endpoint works. Without a key the whole run still works on rules only (`--no-llm`), but the filing-reading step is much weaker |
| About 5 GB of free disk | the two SEC bulk files are large (each is about 1 GB or more) |
| An internet connection | for the one-time downloads and for the LLM |

### 2. Install

```bash
git clone https://github.com/wyhanthu/forecaster-capstone.git
cd forecaster-capstone

python3 -m venv .venv                  # a separate Python environment for this project
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt        # installs the add-ins (packages) below
```

`requirements.txt` installs three add-ins: `langchain-core` and `langchain-openai` (the LLM layer) and `langgraph` (the workflow
graph). `pytest` is only needed for the optional tests at the end of this file. The scoring, retrieval, guardrail and memory
code uses only the Python standard library. Next time, you only need to run `source .venv/bin/activate` again.

### 3. Configure

```bash
cp .env.example .env                   # .env is git-ignored and never committed
```

Open `.env` in any editor and set:

```
SEC_USER_AGENT=Your Name your.email@example.com     # required: SEC asks every client to identify itself
OPENROUTER_API_KEY=your-openrouter-key              # or OPENAI_API_KEY=... (see .env.example)
OPENROUTER_MODEL=provider/model                     # the model must support tool calling and JSON mode
```

Check that the model answers:

```bash
python -m forecaster.cli llm-check     # one tiny call; prints the provider, the model and any error
```

### 4. Download the data (all public)

Forecaster needs two SEC bulk files. One command downloads both; it reads your contact from `SEC_USER_AGENT` in `.env`
(step 3), so you do not type it again. The files are large, so this takes a while:

```bash
python -m forecaster.cli download-data --dir data --force      # saves data/companyfacts.zip and data/submissions.zip
```

The `screen` command then finds the two files in `data/` by itself, so it needs no file paths. If you prefer `curl`, SEC requires a contact in the
`User-Agent`, so put your own name and e-mail in the command:

```bash
mkdir -p data
curl -A "Your Name you@yourfirm.com" -o data/companyfacts.zip https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
curl -A "Your Name you@yourfirm.com" -o data/submissions.zip https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
```

`--dir` is the folder for the two files (it is created if missing, and `data/` is git-ignored). Without `--dir` the default is also `data/`; add `--force` to download again.

Do not unzip them; Forecaster reads the zip files directly. If a download fails, open the SEC page
https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data in a browser and download `companyfacts.zip` and
`submissions.zip` from there. Re-download them whenever you want fresher filings.

Already in this repository, so there is nothing else to download:
- `ratings.csv`: real credit ratings for 301 companies (Egan-Jones, public, from https://ratingshistory.info). Optional input.
- `forecaster/data/industry_map.csv`: industry and sector for the concentration caps (public Nasdaq.com screener data).

Details and how to refresh them are in "Data sources" below. Filing text (10-K, 10-Q, 8-K) is fetched from sec.gov for the
shortlisted pool only and cached in `forecaster_state/`, so you do not download it yourself.

### 5. Run

CLO mode, 50 names:

```bash
python -m forecaster.cli screen --mode clo --maturity 2028-12-31 --top-n 50 --ratings-file ratings.csv --output clo_rated.md
```

RegCap mode, 35 names:

```bash
python -m forecaster.cli screen --mode regcap --maturity 2028-12-31 --top-n 35 --ratings-file ratings.csv --output regcap_rated.md
```

| Option | Meaning |
|---|---|
| `--mode clo` or `regcap` | which rating target and quotas to use (see "Modes and rating targets") |
| `--maturity YYYY-MM-DD` | the date the collateral must last until |
| `--top-n N` | how many names you want in the final list |
| `--pool-factor 3` | the LLM reads 3 x N companies, then the final N are chosen under the caps |
| `--ratings-file ratings.csv` | optional; real ratings override the implied ones |
| `--facts-zip`, `--submissions-zip` | optional; default `data/companyfacts.zip` and `data/submissions.zip` from step 4 |
| `--output file.md` | the report; a `.json` copy is written next to it |
| `--no-llm` | rules only, no key needed |

What you will see: stage 1 screens all companies (about 2 minutes, no LLM), then stage 2 reads the filings of the pool (roughly
10 to 20 minutes with the default settings, depending on the model). The run ends by printing how many LLM calls were made and how
many failed. If the run stops, run the same command again: downloaded filings are cached.

Without the zip files the same command still works, but it reads one record per company from SEC (about 10 minutes, cached).

### 6. Read the report

The output file has: the funnel counts, the **shortlist** with a concentration table, one **memo per company** (verbatim
filing facts, computed ratios, numbered citations), the names **not selected** and why, and the **human review queue**. A name is
marked `review` when it has an open question for the analyst. To record your decision and lessons for the next run, see
"Memory and human review".

### 7. Try it without any data or key (optional)

```bash
python -m forecaster.cli screen --top-n 8 --output report.md      # offline demo on 14 fictional companies
```

## What it does

1. **Universe (`universe.py`).** Every company with XBRL facts that has a ticker on NYSE or Nasdaq, filed a 10-K or 10-Q in
   the last 150 days, is not financial or real estate (by SIC code, and again by the Nasdaq.com sector), is kept: about 3,160
   companies. The funnel counts are printed in every report and the universe is cached (`--rebuild-universe`).
2. **Stage 1, cheap pre-screen (no LLM, no filing text).** Leverage, interest coverage, current ratio and a liquidity cover
   are computed from XBRL facts. A company must be inside the rating target, have enough debt and pass the thresholds.
   Survivors are ranked by a financial score, and a **pool of `--pool-factor` x the target size** (default 3x) is kept, with
   quotas by rating group so the pool is not all one kind of name.
3. **Stage 2, reading (LLM).** Only the pool has its filing text fetched and read: MD&A, risk factors, the debt footnote and
   six 10-Q notes. A ReAct retrieval loop gathers cited evidence; ambiguous cases go through a selective Tree-of-Thought;
   a Validator vetoes anything uncited or unsupported. Each company ends as include, exclude, uncertain or insufficient data.
4. **Portfolio.** Included companies are ranked and the shortlist is built under concentration caps: **at most 15% per
   industry** and **40% per sector**, plus sponsor and maturity-wall caps. An industry is limited to one name only when a
   company's own filing says the industry as a whole is weak. Every report ends the shortlist with a concentration table.
5. **Human review.** A review queue lists shortlisted names with open questions; decisions and lessons are stored and
   used in the next run (see "Memory").

Per-company limits: 120 tool calls, 20 LLM calls, two revision rounds. A bad filing costs one company, not the batch.

## Modes and rating targets

SEC filings carry **no credit ratings**, so the category is *implied* from Debt / LTM EBITDA and interest coverage
(`rating.py`; bands calibrated on 225 Egan-Jones rated companies, where the exact category matches 32% of the time and
within one category 61%). Ratios cannot separate A, BBB+ and BBB, so an implied rating gates by **group** only (top / core /
BB / weaker); the notch is judged only from a real rating.

| Mode | Target | Pool quotas | Debt floor |
|---|---|---|---|
| `regcap` | BBB+ or better (AA or better, A, BBB+) | 70% core, 30% top | Debt/EBITDA at least 0.2x |
| `clo` | A (ceiling) down to BB, mainly BBB | 80% core, 20% BB | Debt/EBITDA at least 0.5x (a CLO lives on coupon) |

`--ratings-file ratings.csv` (`ticker,rating`, S&P / Fitch / Moody's symbols) overrides the implied category for the
names it lists, and a real rating must match its exact category. `ratings.csv` in this repository was built with `import-ratings` from the public Egan-Jones 17g-7 rating history
(see "Data sources"); `calibrate-ratings` compares implied with real ratings. An optional size floor is
available with `--min-ebitda` (LTM EBITDA in $ millions; off by default), and `--min-leverage` overrides the debt floor.

## Data sources (all public)

| Data | Where to get it | How it is used |
|---|---|---|
| XBRL facts and filings index | SEC bulk files: `companyfacts.zip` (https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip) and `submissions.zip` (https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip); page: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | `--facts-zip`, `--submissions-zip` |
| Filing text (10-K, 10-Q, 8-K) | fetched live from sec.gov for the pool only, then cached | stage 2 reading |
| Credit ratings (optional) | Egan-Jones 17g-7 rating history, https://ratingshistory.info; convert with `python -m forecaster.cli import-ratings <history.csv> --output ratings.csv --facts-zip ... --submissions-zip ...` | `--ratings-file ratings.csv` |
| Industry and sector | Nasdaq.com stock screener, https://www.nasdaq.com/market-activity/stocks/screener (download the CSV for each exchange) | bundled as `forecaster/data/industry_map.csv`; refresh with `--industry-file` |

To download the two SEC files, run `python -m forecaster.cli download-data` (step 4 above; it uses `SEC_USER_AGENT` from `.env`).
Without the zip files the same screen command still works but reads one record per company from SEC (about 10 minutes, cached).

## Industry and sector (public data only)

Industry and sector for the concentration caps come from the public Nasdaq.com stock-screener downloads for NASDAQ, NYSE and
NYSE American (Sector and Industry columns), bundled without prices in `forecaster/data/industry_map.csv`. This is Nasdaq's
own taxonomy (13 sectors, about 140 industries), **not GICS**. A ticker missing from the file falls back to its SEC SIC group.
To refresh, download the screener CSVs again and pass them with `--industry-file`. No licensed or non-public data is used.

## LLM layer

Built on LangChain (`llm.py`): `ChatOpenAI` is the chat model, the ReAct retriever calls it with `bind_tools` (function
calling), and structured answers run as a chain `ChatPromptTemplate | chat model (JSON mode) | JsonOutputParser`. Token usage
is written to the audit log with each call.

```
# .env
OPENROUTER_API_KEY=...           # or OPENAI_API_KEY (any OpenAI-compatible endpoint with OPENAI_BASE_URL)
OPENROUTER_MODEL=provider/model  # must support tool calling and JSON mode
LLM_MAX_RETRIES=6                # optional: retries with backoff on connection errors
```

`python -m forecaster.cli llm-check` makes one tiny call and prints the provider, model and any error.

Where the LLM is used, and where it may not decide:
1. **ReAct retrieval** (`agents.LLMRetriever`): Thought, tool call, Observation each round; a premature DONE is pushed back;
   the loop has a step limit and a rule-based safety net. The company (CIK) is fixed and every call goes through
   `ToolRegistry.mcp_call` (budget, retry, audit).
2. **Reading ambiguous filings** (`agents.Analyst`): for each hypothesis (stable, setback, deterioration) the model says
   whether the text supports it, whether the weakness is company-specific or industry-wide, and quotes the proof.
3. **Never trusted blindly.** The LLM never computes or writes a number; quotes must be verbatim (checked by the Analyst and
   again by the Validator); a rationale containing a digit is discarded; a model "no" vetoes the branch.
4. **Budget and logging** (`llm.LLMGate`): calls per company are capped, and every call and rejected quote is in `audit.jsonl`.
   If a call fails, the step falls back to rules and the failure is counted in the run summary.

## LangGraph

The company workflow is a LangGraph `StateGraph` over a typed state: load, ratios, prescreen, events, retrieve, notes,
lessons, reason, then revise (back to retrieve) or finalize, with conditional edges to `finalize` on an early stop.
`python -m forecaster.cli graph` prints it as Mermaid. Without `langgraph` the same nodes run on a small in-house graph
(`graph.py`), and a test checks that both give identical scorecards and routes. Checkpointing and interrupts are not enabled.
CrewAI is not used: the four agents are plain classes with typed hand-offs and a deterministic orchestrator.

## Live data details

| Piece | File | What it does |
|---|---|---|
| HTTP | `edgar.py` `SecHttp` | contact User-Agent, shared limit of about 8 requests/second, retry with backoff on 429/5xx, gzip'd disk cache |
| Source | `edgar.py` `RealEdgar` | ticker to CIK, XBRL facts, 10-K/10-Q text, 8-K events (the same four methods as the offline demo source) |
| XBRL mapping | `xbrl.py` | tag chains with fallbacks; EBITDA = EBIT + D&A (EBIT alone when D&A is missing, noted); quarterly values from 3-month facts or year-to-date differences; debt chains; zero-debt inference; maturities from the debt-maturity tags |
| Filing text | `filing_text.py` | inline-XBRL HTML to text; Items located by longest body; notes split and classified |
| Lazy index | `retrieval.py` | a company's filing text is fetched and indexed only when read, so a company that fails stage 1 costs no filing request |

Cached under `forecaster_state/edgar_cache/` (git-ignored). Diagnostics: `edgar-check AAPL` shows what SEC returned and how it
was mapped; `xbrl-gaps` shows why companies end as "missing facts" and which tags they report instead.

Differences from the demo universe, all deliberate: the required evidence is smaller (real filings give maturities and leases
as tables); Item 1A is read by the LLM but not rule-scanned (its covenant boilerplate would flag everyone); the sponsor is
unknown, so the sponsor cap is inactive; a liquidity cover built without a maturity schedule or capex is flagged and never
vetoes alone; keyword hits from filing text are added to the memo but do not queue a review by themselves.

## The six 10-Q notes

| Note | Used for |
|---|---|
| Debt and maturities | maturities inside the horizon, maturity wall, leverage |
| Credit facilities and covenants | undrawn revolver, covenant headroom (tight = review, breached = veto) |
| Leases and off-balance-sheet | lease outflows; guarantees flagged for review |
| Commitments and contingencies | purchase commitments, possible litigation loss |
| Derivatives and rate exposure | cost of a 100bp rate rise on floating debt |
| Subsequent events / basis of presentation | outlook; going concern (veto) and restatement language |

Their figures become cited facts and feed the adjusted liquidity cover = (cash + revolver + 2 x FCF) / (maturities + leases +
commitments + litigation + 2 x rate shock). A figure the notes do not state stays unknown, never zero.

## Memory and human review

```bash
python -m forecaster.cli review                      # interactive: confirm / include / exclude, then "what did the model miss?"
python -m forecaster.cli review --ticker XYZ --decision exclude --note "..." --lesson "..." --keywords freight --action exclude
python -m forecaster.cli feedback list|add|edit|delete
```
- Lessons live in `forecaster_state/feedback.json` and every decision in the append-only `overrides.jsonl`.
- The next run hands the lessons to the Planner, Retriever and Analyst as hints; keywords are matched against current filing
  text; with an LLM, a lesson fires only if the model quotes the filing verbatim. A lesson set to `review` only flags the name; one set to
  `exclude` removes it only when it fires on the current filing. Lessons never replace evidence.
- Human include/exclude decisions are shown to the next reviewer but are never applied silently.
- A trend library flags deterioration (coverage down two quarters, leverage up three years, free cash flow down three years).
- Known limitation: a lesson is free text, so different analysts write them differently; a fixed template (risk type,
  trigger, action, scope, evidence required) is planned. `forecaster_state/` is local and git-ignored.

## Results

Two live runs on filings as of 2026-11-21, maturity 2028-12-31, with Egan-Jones ratings for 301 names and the rest implied:
- `clo_rated.md`: CLO, 50 names (most industry 8%, most sector 38%), about 1,160 LLM calls, 17 names to human review.
- `regcap_rated.md`: RegCap, 35 names (most industry 9%, most sector 31%), about 760 LLM calls, 12 names to human review.
- The two shortlists share 14 names. Re-running without `--ratings-file` changes 9 of the 50 CLO names and 8 of the 35 RegCap names: four CLO names (real ratings AA or AA-, above the A ceiling) and three RegCap names (real ratings BBB and BB, below BBB+) leave because of a real rating; the rest is replacement and model variation. Most names still rest on implied ratings, so the results show the pipeline and its caps
  work at scale, not that every name carries the targeted rating.
- `sample_report.md` is the offline demo on the synthetic universe.

## Design to code

| Design | Where |
|---|---|
| Funnel: XBRL pre-screen, scorecards, diversified shortlist | `workflow.py` (company loop, two-stage run), `portfolio.py` |
| Tools: retrieval, verbatim extraction, ratios (no LLM), report | `tools.py`, `retrieval.py`, `ratios.py`, `report.py` |
| No citation, no number; facts vs labeled judgment | `agents.Validator`, `report.py` |
| Short-term notebook, trend library, feedback memory | `memory.py` |
| Human review and experience memory | `review.py`, `cli.py`, `workflow.n_lessons` |
| Structure-aware chunks, hybrid retrieval, MMR, ReAct | `retrieval.py`, `agents.LLMRetriever` |
| Selective ToT (beam 3, depth 2), setback vs deterioration | `tot.py`, `agents.py` |
| Planner / Retriever / Analyst / Validator + orchestrator | `agents.py`, `workflow.py`, `graph.py` |
| Rating targets, implied ratings, calibration | `rating.py`, `calibrate.py`, `ratings_import.py` |
| Budgets, retry, event freeze, caps, breaker, audit | `tools.py`, `guardrails.py`, `portfolio.py`, `audit.py` |

## Limitations

- Ratings are implied from ratios for most names; the notch inside A / BBB+ / BBB cannot be resolved without real ratings.
- About 900 of 3,159 companies lack usable XBRL facts (no interest tag, custom tags, combined filers) and end as
  insufficient data, which favors large, well-disclosing issuers.
- Industry is Nasdaq.com's taxonomy, not GICS; retrieval is TF-IDF, not embeddings; there is no backtest against later
  downgrades or defaults; thresholds were calibrated on one rating file.
- The first-pass screen needs a human: the review queue exists for that reason.

## Layout

`forecaster/` the package (code, `data/industry_map.csv`) | `tests/` 134 tests | `requirements.txt` | `.env.example` |
`ratings.csv` real ratings for 301 companies |
`clo_rated.md`, `regcap_rated.md`, `sample_report.md` example outputs.

## Tests (optional, for developers)

```bash
python -m pytest -q                    # 134 offline tests, no network and no key needed
```
