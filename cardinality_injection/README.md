# Cardinality Injection

Replaces the PostgreSQL optimizer's estimated join cardinalities with measured
true values, to test how much of the cost–runtime gap is caused by cardinality
error rather than by mispriced cost units.

**Requires [pg_lab](https://github.com/rbergm/pg_lab)** — a PostgreSQL research
fork whose `Card()` hint can override the cardinality of an arbitrary relation
set. Stock PostgreSQL has no equivalent. The instance used here is PG 18 on
port 5433 (`PORT` in the scripts).

Ground truth is measured on the IMDB dataset and is **not** specific to
pg_lab, or even to PostgreSQL: the number of rows a join produces is a property
of the data. The JSON files under `data/cardinalities/` are reusable for the
MySQL and DB2 arms of the evaluation, or for scoring a learned estimator.

## Layout

```
paths.py                  canonical file locations (edit here, not in scripts)
parse_join_graph.py       SQL -> join graph
job_schema_graph.py       IMDB schema (PK/FK edges)
enumerate_subplans.py     graph -> every connected subset -> COUNT(*) query
get_true_cardinalities.py run those counts (any PostgreSQL)
precompute_all.py         whole-workload collection, resumable
complete_query.py         fill gaps for one query, parallel; idempotent
build_card_hints.py       cardinalities -> /*=pg_lab= Card(...) */ block
run_measurements.py       2x2: {default,calibrated} units x {default,injected} cards
ab_interleaved.py         controlled A/B (interleaved arms + Wilcoxon)
analyze_measurements.py   rank correlations from measurements.csv
run_full_measurement.sh   serialized full run, applies/reverts procost

data/cardinalities/       33 x <query>_true_cardinalities.json  (ground truth)
data/gaps/                subplans with no exact count
data/results/             measurements.csv, ab_results.json
data/plans/               saved EXPLAIN ANALYZE dumps
```

## Running

Collection (works against any PostgreSQL holding `imdb`):

```
python3 precompute_all.py 90        # whole workload, per-subplan timeout 90s
python3 complete_query.py 29b 4 300 # fill one query's gaps: 4 workers, 300s
```

Measurement and A/B (pg_lab only — these apply `Card()` hints):

```
./run_full_measurement.sh                    # full 2x2 over all 33 queries
python3 ab_interleaved.py 15 2c 17e 27c ...  # 15 interleaved rounds per query
python3 analyze_measurements.py
```

## Do not disable parallel query execution during collection

`precompute_all.py` inherited `max_parallel_workers_per_gather = 0` from the
calibration scripts, where serial execution is required so probe timings stay
deterministic. For collecting `COUNT(*)` ground truth that setting is actively
harmful: it forces nested-loop plans over the mid-size joins. Subplans that
timed out after 120s serial complete in about **1 second** with 4 parallel
workers. `complete_query.py` enables parallelism; `precompute_all.py` does not.

Collecting all 23,475 subplans takes roughly an hour with parallelism, and the
better part of a day without it.

## Coverage

23,475 of 23,558 subplans (99.65%). 30 of 33 queries are complete; 28b, 29b and
30c are at 99.8%, 99.5% and 99.0%. The 83 unreachable subplans are top-of-tree
joins that exceed 300s even with parallelism — they keep the optimizer's own
estimate, which is valid because `Card()` hints apply per-subplan and an
incomplete hint set degrades gracefully. `data/gaps/` records exactly which
subplans these are, and `29b_lower_bound_subplans.json` lists 14 entries that
are LIMIT-capped lower bounds rather than exact counts.

## Results

Injection lifts the cost/runtime rank correlation from 0.828 to 0.928 across the
32 non-outlier queries, and 21 of 33 queries get significantly faster plans
under a controlled interleaved A/B (workload total 30.5s -> 21.9s). The gains
come from plan changes — mainly nested loops becoming hash joins once the
optimizer stops underestimating join sizes, plus Memoize nodes appearing where
the true outer row count justifies caching.
