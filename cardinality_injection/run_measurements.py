"""Measure estimated cost vs actual runtime for JOB_REFINED queries on the
pg_lab instance, under a 2x2 of conditions:

  units: default cost GUCs  |  calibrated cost GUCs (Wu-style, pg_lab-specific)
  cards: optimizer's own estimates  |  true cardinalities injected (Card hints)

For each (query, units, cards): one warm-up execution, then EXPLAIN for the
estimated total cost and N EXPLAIN ANALYZE reps for actual ms (median).
jit=off everywhere: JIT compile time is a constant overhead the cost model
does not price, and the thesis's PG14 baseline had no JIT.

Output: measurements.csv
"""
import csv
import json
import os
import statistics
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
WL = paths.WORKLOAD
CALIB_JSON = paths.CALIB_UNITS
PORT = 5433
REPS = 3

sys.path.insert(0, HERE)
from build_card_hints import build_hint_block  # noqa: E402

import paths


def load_calibrated_gucs():
    with open(CALIB_JSON) as f:
        data = json.load(f)
    gucs = dict(data["gucs"])
    ext_path = paths.CALIB_EXT
    if os.path.exists(ext_path):
        with open(ext_path) as f:
            ext = json.load(f)
        if "parallel_setup_cost" in ext:
            gucs["parallel_setup_cost"] = ext["parallel_setup_cost"]["units"]
        if "parallel_tuple_cost" in ext:
            gucs["parallel_tuple_cost"] = ext["parallel_tuple_cost"]["units"]
        if "effective_cache_size" in ext:
            gucs["effective_cache_size"] = f"{ext['effective_cache_size']['recommended_gb']}GB"
    return gucs


def apply_units(cur, units):
    cur.execute("set jit = off")
    if units == "calibrated":
        for k, v in load_calibrated_gucs().items():
            cur.execute(f"set {k} = '{v}'")
    # default: fresh session already has stock values


def join_order_fingerprint(plan_node):
    """Nested parenthesized relation names, to detect plan flips."""
    rel = plan_node.get("Relation Name")
    children = [join_order_fingerprint(c) for c in plan_node.get("Plans", [])]
    children = [c for c in children if c]
    if rel and not children:
        return plan_node.get("Alias", rel)
    if len(children) == 1:
        return children[0]
    if children:
        return "(" + " ".join(children) + ")"
    return ""


def measure(query_name, units, cards):
    sql = open(os.path.join(WL, f"{query_name}.sql")).read().strip().rstrip(";")
    if cards == "injected":
        with open(paths.cardinalities(query_name)) as f:
            hint = build_hint_block(json.load(f))
        sql = hint + "\n" + sql

    conn = psycopg2.connect(dbname="imdb", port=PORT)
    conn.autocommit = True
    cur = conn.cursor()
    apply_units(cur, units)

    cur.execute("explain (format json) " + sql)
    plan = cur.fetchone()[0][0]["Plan"]
    est_cost = plan["Total Cost"]
    fingerprint = join_order_fingerprint(plan)

    cur.execute(sql)  # warm-up execution (also warms cache like benchmark runs)

    times = []
    for _ in range(REPS):
        cur.execute("explain (analyze, timing off, format json) " + sql)
        times.append(cur.fetchone()[0][0]["Execution Time"])
    conn.close()
    return est_cost, statistics.median(times), fingerprint


if __name__ == "__main__":
    # UNITS=default|calibrated runs just that pass (so catalog-level procost
    # ALTERs can be applied/reverted around the calibrated pass); rows append.
    units_filter = os.environ.get("UNITS", "")
    unit_conditions = (units_filter,) if units_filter else ("default", "calibrated")
    queries = sys.argv[1:]
    out_path = paths.MEASUREMENTS
    write_header = not os.path.exists(out_path)
    rows = []
    for units in unit_conditions:
        for q in queries:
            for cards in ("default", "injected"):
                est, ms, fp = measure(q, units, cards)
                rows.append([q, units, cards, est, ms, fp])
                print(f"{q:5s} units={units:10s} cards={cards:8s} "
                      f"est_cost={est:>14.0f} actual={ms:9.1f}ms", flush=True)
    with open(out_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["query", "units", "cards", "est_cost", "actual_ms", "join_order"])
        w.writerows(rows)
    print(f"\nappended to {out_path}")
