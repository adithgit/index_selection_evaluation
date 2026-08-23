"""Same-session interleaved A/B to certify whether cardinality injection
actually lowers runtime, controlling for cache warming.

Both arms use the SAME calibrated cost units (+procost, applied at catalog
level by the caller). The ONLY difference between arm A and arm B is whether
true-cardinality Card() hints are present. Within each round we run both arms
back-to-back, alternating which arm goes first, so the two arms share cache
state round-for-round. We collect paired per-round timings and run a Wilcoxon
signed-rank test (paired, non-parametric) on the per-round differences.

Usage: python3 ab_interleaved.py <rounds> <q1> <q2> ...
"""
import json
import os
import statistics
import sys

import psycopg2
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
WL = paths.WORKLOAD
CALIB = paths.CALIB_UNITS
EXT = paths.CALIB_EXT
PORT = 5433

sys.path.insert(0, HERE)
from build_card_hints import build_hint_block  # noqa: E402

import paths


def calibrated_gucs():
    g = dict(json.load(open(CALIB))["gucs"])
    e = json.load(open(EXT))
    g["parallel_setup_cost"] = e["parallel_setup_cost"]["units"]
    g["parallel_tuple_cost"] = e["parallel_tuple_cost"]["units"]
    g["effective_cache_size"] = f"{e['effective_cache_size']['recommended_gb']}GB"
    return g


def prepare(cur):
    cur.execute("set jit = off")
    for k, v in calibrated_gucs().items():
        cur.execute(f"set {k} = '{v}'")


def sql_for(q, injected):
    s = open(os.path.join(WL, f"{q}.sql")).read().strip().rstrip(";")
    if injected:
        hint = build_hint_block(json.load(open(paths.cardinalities(q))))
        s = hint + "\n" + s
    return s


def timed(cur, sql):
    cur.execute("explain (analyze, timing off, format json) " + sql)
    return cur.fetchone()[0][0]["Execution Time"]


def run(rounds, queries):
    conn = psycopg2.connect(dbname="imdb", port=PORT)
    conn.autocommit = True
    cur = conn.cursor()
    prepare(cur)

    results = {}
    for q in queries:
        sql_plain = sql_for(q, injected=False)   # arm A: calibrated only
        sql_inj = sql_for(q, injected=True)      # arm B: calibrated + injected

        # warm both plans into cache once (not measured)
        cur.execute(sql_plain)
        cur.execute(sql_inj)

        a_times, b_times = [], []
        for r in range(rounds):
            if r % 2 == 0:
                a = timed(cur, sql_plain); b = timed(cur, sql_inj)
            else:
                b = timed(cur, sql_inj); a = timed(cur, sql_plain)
            a_times.append(a); b_times.append(b)

        diffs = [a - b for a, b in zip(a_times, b_times)]  # +ve => injected faster
        med_a, med_b = statistics.median(a_times), statistics.median(b_times)
        try:
            w_stat, p = stats.wilcoxon(a_times, b_times)
        except ValueError:
            p = float("nan")
        pct = (1 - med_b / med_a) * 100
        results[q] = dict(med_cal=med_a, med_inj=med_b, pct=pct,
                          med_diff=statistics.median(diffs), p=p, n=rounds)
        print(f"{q:5s} cal={med_a:8.1f}ms  inj={med_b:8.1f}ms  "
              f"faster={pct:6.1f}%  median_diff={statistics.median(diffs):+7.1f}ms  "
              f"p={p:.4f}", flush=True)

    conn.close()
    return results


if __name__ == "__main__":
    rounds = int(sys.argv[1])
    queries = sys.argv[2:]
    res = run(rounds, queries)
    with open(paths.AB_RESULTS, "w") as f:
        json.dump(res, f, indent=2)

    print("\n=== summary (Wilcoxon signed-rank, paired per-round) ===")
    sig = [q for q, r in res.items() if r["p"] < 0.05 and r["pct"] > 0]
    print(f"queries where injection is significantly faster (p<0.05): {sorted(sig)}")
    ns = [q for q, r in res.items() if not (r["p"] < 0.05 and r["pct"] > 0)]
    print(f"not significant / no gain: {sorted(ns)}")
