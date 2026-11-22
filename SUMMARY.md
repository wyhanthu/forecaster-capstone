# Forecaster: project summary

**Goal.** Screen listed US companies from SEC EDGAR filings and produce a diversified, cited shortlist of obligors for a
two-year ABS / CLO collateral pool. Inputs: maturity date, number of companies, mode (RegCap or CLO).

**Principles.** Read-only environment; no citation, no number; facts and labeled judgments kept apart; the LLM never writes a
number; every LLM step falls back to rules; a bad filing costs one company, not the batch.

## Pipeline
1. Universe: about 3,160 non-financial NYSE / Nasdaq companies with a recent filing, from the SEC bulk files.
2. Stage 1: ratios from XBRL (no LLM); rating group implied from leverage and coverage (or taken from a ratings file); debt
   floor; ranking; a pool of about 3x the target size with rating-group quotas.
3. Stage 2: only the pool is read: ReAct retrieval, selective Tree-of-Thought, six 10-Q notes, Validator vetoes.
4. Portfolio: at most 15% per industry and 40% per sector (Nasdaq.com public taxonomy), sponsor and maturity-wall caps; a
   concentration table in the report.
5. Human review queue; decisions and lessons stored and reused in the next run.

## Data sources (all public)
- SEC bulk files `companyfacts.zip` and `submissions.zip` (sec.gov, free; pass a contact e-mail as User-Agent), plus filing text fetched live for the pool only.
- Egan-Jones rating history from ratingshistory.info (optional, `import-ratings` turns it into `ratings.csv`).
- Nasdaq.com stock-screener CSVs for industry and sector (bundled as `forecaster/data/industry_map.csv`).
The README gives the download links and commands.

## Design to code
| Design element | Code |
|---|---|
| Company workflow (LangGraph) | `workflow.py`, `graph.py` |
| Ratios and implied ratings | `ratios.py`, `xbrl.py`, `rating.py` |
| Retrieval and ReAct | `retrieval.py`, `agents.py` |
| Tree-of-Thought | `tot.py` |
| Validator and guardrails | `agents.py`, `guardrails.py` |
| Portfolio caps and report | `portfolio.py`, `report.py` |
| Memory and human review | `memory.py`, `review.py` |
| Tools, SEC client, audit | `tools.py`, `edgar.py`, `audit.py` |
| LLM (LangChain, OpenRouter or OpenAI) | `llm.py` |

## Targets
- RegCap: BBB+ or better; debt floor 0.2x Debt/EBITDA.
- CLO: A at most (no AA or AAA) down to BB, mainly BBB; debt floor 0.5x.
- An implied rating gates by group only (calibrated on 225 Egan-Jones names: 32% exact, 61% within one category); a real rating
  must match its category.

## Results (filings as of 2026-11-21, maturity 2028-12-31)
| | CLO | RegCap |
|---|---|---|
| Shortlist | 50 | 35 |
| Companies reaching include | 143 | 102 |
| Largest industry / sector share | 8% / 38% | 9% / 31% |
| LLM calls (approx.) | 1,160 | 760 |
| Names to human review | 17 | 12 |

The two shortlists share 14 names. Without the rating file, 9 of the 50 CLO names and 8 of the 35 RegCap names change: four CLO names (real ratings AA or AA-) and three RegCap names (real ratings BBB and BB) leave because of a real rating, and the rest is replacement and model variation. Egan-Jones ratings (public, from ratingshistory.info) were supplied for 301 names; the rest are implied, so the results show
that the pipeline and caps work at scale, not that every name carries the targeted rating.

## Limitations and next steps
- Ratings: obtain ratings for the whole universe, calibrate bands on a larger sample, and backtest against later outcomes.
- About 900 companies lack usable XBRL facts; close the gaps (custom tags, combined filers) and extract maturities from text.
- Analyst lessons are free text; standardize them with a fixed template filled by the LLM and confirmed by the analyst.
- Industry data is Nasdaq.com's taxonomy, not GICS; retrieval is TF-IDF; add embeddings for the pool only.
- Enable LangGraph checkpointing and human-in-the-loop interrupts.
