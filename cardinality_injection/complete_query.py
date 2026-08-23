"""Fill in any missing subplan cardinalities for a given query, with intra-query
parallelism enabled.

Computes the missing set directly from the enumeration (rather than trusting a
stale *_skipped_subplans.json), so it is safe to re-run at any time and is
idempotent once a query is complete.

Usage: python3 complete_query.py <query> [parallel_workers] [timeout_s]
"""
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

if __name__ == "__main__":
    q = sys.argv[1]
    parallel = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    timeout_s = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    sql = open(os.path.join(WL, f"{q}.sql")).read()
    qmap = {",".join(sorted(s)): cq for s, cq in generate_all(sql)}
    path = paths.cardinalities(q)
    results = json.load(open(path)) if os.path.exists(path) else {}
    missing = [k for k in qmap if k not in results]
    if not missing:
        print(f"{q}: already complete ({len(results)}/{len(qmap)})")
        sys.exit(0)

    print(f"{q}: {len(results)}/{len(qmap)} present, computing {len(missing)} missing "
          f"({parallel} parallel workers, {timeout_s}s timeout)", flush=True)
    conn = psycopg2.connect(dbname="imdb", port=PORT)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"set max_parallel_workers_per_gather = {parallel}")
    cur.execute(f"set statement_timeout = {timeout_s * 1000}")

    solved, failed = 0, []
    t0 = time.time()
    for i, key in enumerate(missing, 1):
        try:
            cur.execute(qmap[key])
            results[key] = cur.fetchone()[0]
            solved += 1
        except Exception:
            failed.append(key)
        if i % 200 == 0:
            json.dump(results, open(path, "w"))
            print(f"  {i}/{len(missing)} solved={solved} failed={len(failed)}", flush=True)
    conn.close()

    json.dump(results, open(path, "w"), indent=2)
    skip_path = paths.skipped(q)
    if failed:
        json.dump(sorted(failed), open(skip_path, "w"), indent=2)
    elif os.path.exists(skip_path):
        os.remove(skip_path)
    print(f"{q}: {len(results)}/{len(qmap)} subplans "
          f"({100*len(results)/len(qmap):.1f}%) in {time.time()-t0:.1f}s, "
          f"solved={solved} failed={len(failed)}", flush=True)
