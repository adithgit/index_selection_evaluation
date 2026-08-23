"""Precompute true cardinalities for the whole JOB_REFINED workload.

Differences from precompute_batch.py:
  * per-subplan statement_timeout, so one monster COUNT(*) cannot stall the run.
    A timed-out subplan is simply omitted -- pg_lab applies Card() hints
    per-subplan, so a partial hint set is still valid (the optimizer keeps its
    own estimate for anything unhinted).
  * writes incrementally after every query, so the run is resumable and a kill
    never loses completed work.
  * processes queries smallest-subplan-count first.

Usage: python3 precompute_all.py [per_subplan_timeout_seconds]
"""
import glob
import json
import os
import sys
import time

import psycopg2

from enumerate_subplans import generate_all

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
WL = paths.WORKLOAD
PORT = 5433
TIMEOUT_S = int(sys.argv[1]) if len(sys.argv) > 1 else 120


def subplan_count(q):
    return len(generate_all(open(os.path.join(WL, f"{q}.sql")).read()))


def run_query(q, timeout_s):
    sql = open(os.path.join(WL, f"{q}.sql")).read()
    plans = generate_all(sql)
    conn = psycopg2.connect(dbname="imdb", port=PORT)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"set statement_timeout = {timeout_s * 1000}")
    results, skipped = {}, []
    for subset, cq in plans:
        key = ",".join(sorted(subset))
        try:
            cur.execute(cq)
            results[key] = cur.fetchone()[0]
        except psycopg2.errors.QueryCanceled:
            skipped.append(key)
            conn.rollback() if not conn.autocommit else None
        except Exception:
            skipped.append(key)
    conn.close()
    return results, skipped, len(plans)


if __name__ == "__main__":
    # 29b (17 tables, 13,246 connected subplans) is excluded: it alone is ~58%
    # of the workload's total subplan count and its subplans are the largest
    # joins in JOB, making brute-force ground truth intractable here.
    SKIP = {"29b"}

    all_q = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(WL, "*.sql")))
    todo = []
    for q in all_q:
        if q in SKIP:
            continue
        if os.path.exists(paths.cardinalities(q)):
            continue
        todo.append((subplan_count(q), q))
    todo.sort()
    print(f"{len(all_q)} queries total, {len(todo)} still to compute "
          f"(per-subplan timeout {TIMEOUT_S}s)", flush=True)

    for n, q in todo:
        t0 = time.time()
        res, skipped, total = run_query(q, TIMEOUT_S)
        dt = time.time() - t0
        with open(paths.cardinalities(q), "w") as f:
            json.dump(res, f, indent=2)
        note = f"  ({len(skipped)} timed out)" if skipped else ""
        print(f"{q:5s} {len(res)}/{total} subplans in {dt:7.1f}s{note}", flush=True)
        if skipped:
            with open(paths.skipped(q), "w") as f:
                json.dump(skipped, f, indent=2)
    print("done", flush=True)
