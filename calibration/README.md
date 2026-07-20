# PostgreSQL cost-model calibration

Calibrates the optimizer's five cost units to the local hardware using the
profiling method of Wu et al., *"Predicting Query Execution Time: Are Optimizer
Cost Models Really Unusable?"* (ICDE 2013): CPU units measured **warm** (in
`shared_buffers`, no I/O), I/O units measured **cold** (real disk reads).

## Applying it — trigger calibrated costs, or use defaults

Controlled by the `PG_COST_GUCS` environment variable, read by
`selection/dbms/postgres_dbms.py`. The units are passed as libpq startup options,
so they apply to **every** what-if `EXPLAIN` during index selection *and* every
timed query.

| `PG_COST_GUCS` | Behaviour |
|----------------|-----------|
| *(unset)* | **Stock PostgreSQL defaults** (`random_page_cost=4`, …) |
| `calibrated` | Load `calibration/postgres_cost_units.json` |
| `"random_page_cost=80;cpu_tuple_cost=0.035"` | Explicit `;`-separated GUCs |

```bash
# default cost model
python -m selection.index_selection_evaluation example_configs/config_...json

# calibrated cost model
PG_COST_GUCS=calibrated python -m selection.index_selection_evaluation example_configs/config_...json
```

`BENCHMARK_RESULT_SUFFIX=_calibrated` (read by `selection/benchmark.py`) keeps the
calibrated result CSVs separate from the default ones.

## Regenerating the calibration

Two phases, because the I/O units must be measured on a **cold** cache:

```bash
python calibration/calibrate_1_cpu_warm.py          # builds tables, measures CPU units warm
brew services restart postgresql@15 && sleep 4 && sudo purge   # clear shared_buffers + OS cache
python calibration/calibrate_2_io_cold.py           # measures I/O cold, writes postgres_cost_units.json
```

Phase 2 prints read-vs-hit blocks so you can confirm the reads were genuinely cold
(`hit_blocks` should be ~0). Intermediate state lives in `calibration/_partial.json`.

## Current calibration (see `postgres_cost_units.json`)

`random_page_cost` ≈ **80** (vs default 4): on cold reads a random page is ~80× a
sequential one. **Caveat:** this is the *cold*-disk cost; a cache-resident workload
sees random ≈ sequential, so no single `random_page_cost` is right for both. With
only these 5 units calibrated ("v1"), the effect on JOB is roughly runtime-neutral
and nudges the cost↔runtime rank correlation 0.18 → 0.27 — partial calibration
fixes cost *units*, not *cardinality*, which is JOB's real problem.

## Full calibration ("v2") — adds parallel costs, honest cache size, and procost

Applying the 5 core units together with `parallel_setup_cost=1397`,
`parallel_tuple_cost=0.0069`, `effective_cache_size=12GB`, and the string-function
`procost` overrides (see below) gives a materially different, and materially
better-on-JOB, picture than v1 alone. Measured on **actual wall-clock query
runtime** (not cost ratios, which can hide baseline artifacts across sessions):

| workload | algorithm | actual runtime, default → v2 (full) |
|---|---|---|
| JOB | relaxation | 640.8s → 501.7s (**−21.7%**, faster at 12/15 budgets) |
| JOB | db2advis | 3399.2s → 586.5s (**−82.7%**, faster at 14/15 budgets — mostly from eliminating timeout blowups) |
| TPC-H | relaxation | 6688.8s → 6775.5s (**+1.3%**, slightly slower, faster at only 5/14 budgets) |
| TPC-H | db2advis | 6363.3s → 7033.9s (**+10.5%**, slower, faster at only 1/14 budgets) |

So full calibration is a genuine win on JOB and does **not** help (mildly hurts)
on TPC-H — an asymmetry likely explained by JOB having costly plan blowups for
calibration to eliminate (db2advis's timeouts) while TPC-H had none. When
comparing default vs. calibrated runs from different sessions, prefer this direct
actual-seconds comparison over cost-ratio "speedup" (sp*) — the ratio's own
no-index baseline can differ enough across sessions to show false parity or false
improvement even when raw runtime moved the other way.

## Extended calibration (parameters the 5-unit model leaves out)

`calibrate_3_extended.py` measures the cost-relevant parameters beyond the five units;
results in `postgres_cost_units_extended.json`. Findings on this machine:

| parameter | measured | PG default | verdict |
|---|---|---|---|
| `parallel_setup_cost` | ~1,397 units (1.6 ms) | 1000 | default ~right |
| `parallel_tuple_cost` | 0.0069 units (8 ns) | 0.1 | default overprices 14x |
| `procost(texteq)` | 1.8 | 1 | close |
| `procost(textlike)` | 13 | 1 | LIKE underpriced 13x |
| `procost(textregexeq)` | 80 | 1 | regex underpriced 80x |
| `effective_io_concurrency` | locked at 0 | 0 | macOS lacks posix_fadvise |
| `jit_*_cost` | n/a | 100k/500k | JIT unavailable in this build |
| `effective_cache_size` | ~12 GB (capacity) | 4 GB | state itself is not a constant |

To apply the string-function costs to a database (catalog change, per-DB):
```sql
ALTER FUNCTION texteq(text,text) COST 1.8;
ALTER FUNCTION textlike(text,text) COST 13;
ALTER FUNCTION textregexeq(text,text) COST 80;
```
