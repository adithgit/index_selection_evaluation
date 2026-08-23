# Research extensions

This fork of the index-selection testbed of Kossmann et al. (PVLDB 2020) adds
runtime measurement to an evaluation that was originally cost-only, and then
investigates why estimated cost and measured runtime disagree.

The original framework lives in `selection/`. Everything below is added work.

| area | question | directory |
|---|---|---|
| Cross-DBMS evaluation | Do the algorithms rank the same on measured runtime as on estimated cost? | `selection/dbms/` (DB2 + MySQL connectors), `benchmark_results/` |
| Cost calibration | Is the gap caused by mispriced cost *units*? | `calibration/` |
| Cardinality injection | Is it caused by wrong *cardinalities*? | `cardinality_injection/` |
| Analysis | — | `notebooks/`, `scripts/` |

Full write-up: `HANDOVER_REPORT.docx`.

## Directory guide

```
calibration/
  pg15/       cost units for the PostgreSQL 15 benchmark instance
  pglab/      cost units for the pg_lab PG 18 instance (port 5433)

cardinality_injection/
  *.py        the pipeline (join graph -> subplans -> COUNT(*) -> Card() hints)
  data/       ground truth, gap records, measurement results
              see cardinality_injection/README.md

notebooks/
  explorers/    interactive HTML result browsers (open in a browser)
  dashboards/   plotting scripts for the comparative results
  exploration/  scratch analyses kept for provenance, not maintained

scripts/      data loading, chart generation, extraction helpers
db2/          IBM DB2 container setup
```

## Database instances

Two PostgreSQL servers are involved, and they are not interchangeable:

| instance | version | port | role |
|---|---|---|---|
| main | PostgreSQL 15 | 5432 | index selection, what-if simulation via HypoPG |
| pg_lab | PostgreSQL 18 | 5433 | cardinality injection via `Card()` hints |

HypoPG is **not** currently built against pg_lab, so hypothetical indexes and
cardinality hints cannot yet operate in the same process. Building both
extensions into one binary is the prerequisite for running index selection
under corrected cardinalities.

DB2 and MySQL run in containers (`db2/`, VIDEX for MySQL).

## Reproducing

```bash
# comparative evaluation, default cost model
python3 -m selection example_configs/config_relaxation_job_refined_postgres.json

# calibrated cost model
PG_COST_GUCS=calibrated BENCHMARK_RESULT_SUFFIX=_calibrated \
  python3 -m selection example_configs/config_relaxation_job_refined_postgres.json
```

Cardinality injection has its own instructions in
`cardinality_injection/README.md`, including a warning about parallel execution
that is worth reading before collecting ground truth.
