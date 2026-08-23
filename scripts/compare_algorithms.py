#!/usr/bin/env python3
"""
DB-agnostic comparison of index selection algorithms (db2advis vs extend).

Raw costs and runtimes are NOT comparable across DBMSes:
  - cost units differ (DB2 timerons, Postgres planner units, MySQL cost units),
  - runtimes depend on engine/hardware (MySQL here runs under Rosetta emulation).
Every metric below therefore normalizes per query against the no_index baseline
measured on the SAME DBMS, which yields dimensionless ratios that can be
aggregated and compared across systems.

Censoring: a null runtime in the CSV means the query hit the per-query timeout
(60 s for the db2/postgres runs, 300 s for mysql) or the connection failed.
These are right-censored, not missing at random — dropping them would reward
algorithms that produce catastrophic plans. Policy "impute" replaces them with
the censoring limit (a conservative lower bound on the true runtime); policy
"drop" excludes the query pairwise. Both are reported as a sensitivity check.

Metrics per (db, algorithm, budget):
  gm      geometric mean of per-query speedups  s_q = rt_baseline / rt_algo
  total   workload speedup = sum(rt_baseline) / sum(rt_algo)
  median  median per-query speedup
  wins/losses  count of queries >10% faster / >10% slower than baseline
  worst   worst per-query slowdown (max rt_algo / rt_baseline)
  cost_red     estimated total cost reduction vs baseline (within-DB only)

Cross-DB headline: geometric mean of the per-DB GM speedups at matched budgets.
"""

import csv
import json
import math
import glob
import sys
from collections import defaultdict

RESULTS_GLOB = "benchmark_results/results_{algo}_JOB_REFINED_{db}_33_queries.csv"
DBS = ["db2", "mysql", "postgres"]
ALGOS = ["db2advis", "extend"]
# per-query timeout used in each run family (ms) — censoring limit for nulls
TIMEOUT_MS = {"db2": 60_000, "postgres": 60_000, "mysql": 300_000}
NOISE_BAND = 0.10  # |speedup-1| <= 10% counts as a tie


def load(path):
    with open(path) as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    header = rows[0]
    qcols = [(i, h) for i, h in enumerate(header) if h.endswith(".sql")]
    out = []
    for r in rows[1:]:
        params = json.loads(r[3].replace("'", '"')) if r[3] else {}
        rec = {"date": r[0], "budget": params.get("budget_MB"),
               "n_idx": int(r[11]) if r[11] else 0, "queries": {}}
        for i, q in qcols:
            cell = json.loads(r[i])
            rt = cell["Runtimes"][0] if cell["Runtimes"] else None
            rec["queries"][q] = {"rt": rt, "cost": cell["Cost"]}
        out.append(rec)
    return out


def baseline_for(db):
    recs = load(RESULTS_GLOB.format(algo="no_index", db=db))
    # use the most recent baseline row without nulls (warm cache)
    for rec in reversed(recs):
        if all(v["rt"] is not None for v in rec["queries"].values()):
            return rec
    return recs[-1]


def speedups(base, rec, db, policy):
    """Per-query speedups s_q = rt_base / rt_algo. Returns dict q -> s."""
    cap = TIMEOUT_MS[db]
    out = {}
    for q, bv in base["queries"].items():
        av = rec["queries"].get(q)
        if av is None or bv["rt"] is None:
            continue
        rt = av["rt"]
        if rt is None:
            if policy == "drop":
                continue
            rt = cap  # censored: true runtime >= cap, so speedup is an upper bound
        out[q] = bv["rt"] / rt
    return out


def gmean(xs):
    xs = [x for x in xs if x > 0]
    if not xs:
        return float("nan")
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return float("nan")
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def sign_test_p(wins, losses):
    """Two-sided exact sign test p-value for paired comparison."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    total = sum(math.comb(n, i) for i in range(0, k + 1)) * 2
    return min(1.0, total / 2 ** n)


def spearman(xs, ys):
    """Spearman rank correlation, no scipy."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    n = len(xs)
    if n < 3:
        return float("nan")
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def analyze(policy):
    baselines = {db: baseline_for(db) for db in DBS}
    data = {(db, algo): load(RESULTS_GLOB.format(algo=algo, db=db))
            for db in DBS for algo in ALGOS}

    print(f"\n{'=' * 100}\nPOLICY: censored runtimes -> {policy}\n{'=' * 100}")
    header = (f"{'db':9s} {'algo':9s} {'budget':>7s} {'n_idx':>5s} "
              f"{'GM speedup':>10s} {'total':>7s} {'median':>7s} "
              f"{'wins':>4s} {'ties':>4s} {'loss':>4s} {'worst slow':>10s} "
              f"{'est cost red':>12s} {'cens':>4s}")
    print(header)
    print("-" * len(header))

    summary = defaultdict(dict)  # (algo, budget) -> db -> gm
    for db in DBS:
        base = baselines[db]
        base_total_cost = sum(v["cost"] for v in base["queries"].values())
        for algo in ALGOS:
            for rec in data[(db, algo)]:
                sp = speedups(base, rec, db, policy)
                vals = list(sp.values())
                wins = sum(1 for s in vals if s > 1 + NOISE_BAND)
                losses = sum(1 for s in vals if s < 1 - NOISE_BAND)
                ties = len(vals) - wins - losses
                cens = sum(1 for v in rec["queries"].values() if v["rt"] is None)
                # total workload speedup over the same query subset
                qs = list(sp.keys())
                bt = sum(base["queries"][q]["rt"] for q in qs)
                at = sum(rec["queries"][q]["rt"] if rec["queries"][q]["rt"] is not None
                         else TIMEOUT_MS[db] for q in qs)
                total = bt / at if at else float("nan")
                algo_cost = sum(v["cost"] for v in rec["queries"].values())
                cost_red = 1 - algo_cost / base_total_cost
                worst = 1 / min(vals) if vals else float("nan")
                gm = gmean(vals)
                summary[(algo, rec["budget"])][db] = gm
                print(f"{db:9s} {algo:9s} {rec['budget']:>7} {rec['n_idx']:>5d} "
                      f"{gm:>10.3f} {total:>7.3f} {median(vals):>7.3f} "
                      f"{wins:>4d} {ties:>4d} {losses:>4d} {worst:>9.2f}x "
                      f"{cost_red:>11.1%} {cens:>4d}")

    # Cross-DB headline at matched budgets
    print(f"\n--- Cross-DB aggregate (geomean of per-DB GM speedups), "
          f"matched budgets {sorted(set(b for _, b in summary if all(db in summary[(a, b)] for a in ALGOS for db in DBS)))} ---")
    print(f"{'budget':>7s}  {'db2advis':>9s} {'extend':>9s}   per-DB detail")
    for budget in sorted({b for _, b in summary}):
        row = []
        detail = []
        for algo in ALGOS:
            per_db = summary.get((algo, budget), {})
            if len(per_db) == len(DBS):
                row.append(gmean(list(per_db.values())))
                detail.append(" ".join(f"{db}:{v:.2f}" for db, v in sorted(per_db.items())))
            else:
                row.append(None)
                detail.append("(incomplete)")
        if any(r is not None for r in row):
            cells = " ".join(f"{r:>9.3f}" if r else f"{'—':>9s}" for r in row)
            print(f"{budget:>7}  {cells}   {' | '.join(detail)}")

    return baselines, data


def per_query(policy, budget):
    baselines = {db: baseline_for(db) for db in DBS}
    print(f"\n{'=' * 100}\nPER-QUERY SPEEDUPS at budget {budget} MB (policy={policy})\n{'=' * 100}")
    cols = []
    for db in DBS:
        for algo in ALGOS:
            recs = [r for r in load(RESULTS_GLOB.format(algo=algo, db=db))
                    if r["budget"] == budget]
            cols.append((db, algo, speedups(baselines[db], recs[0], db, policy) if recs else {}))
    queries = sorted(baselines["db2"]["queries"].keys())
    head = f"{'query':10s}" + "".join(f"{db[:2]}/{a[:6]:>10s}" for db, a, _ in cols)
    print(head + "   consistent-winner")
    for q in queries:
        vals = []
        line = f"{q:10s}"
        for db, a, sp in cols:
            v = sp.get(q)
            vals.append(v)
            line += f"{v:>12.2f}" if v is not None else f"{'—':>12s}"
        # consistency: db2advis better in all DBs / extend better in all DBs
        diffs = []
        for i in range(0, len(cols), 2):
            a, b = vals[i], vals[i + 1]
            if a is not None and b is not None:
                diffs.append(a - b)
        tag = ""
        if len(diffs) == 3:
            if all(d > 0.1 for d in diffs):
                tag = "db2advis (all DBs)"
            elif all(d < -0.1 for d in diffs):
                tag = "extend (all DBs)"
        print(line + ("   " + tag if tag else ""))


def cost_fidelity(policy):
    """Does the DB's estimated cost improvement predict actual speedup?"""
    baselines = {db: baseline_for(db) for db in DBS}
    print(f"\n{'=' * 100}\nCOST-MODEL FIDELITY: Spearman(estimated cost ratio, actual speedup) per run\n{'=' * 100}")
    print(f"{'db':9s} {'algo':9s} {'budget':>7s} {'spearman':>9s}  (n queries)")
    for db in DBS:
        base = baselines[db]
        for algo in ALGOS:
            for rec in load(RESULTS_GLOB.format(algo=algo, db=db)):
                xs, ys = [], []
                for q, bv in base["queries"].items():
                    av = rec["queries"].get(q)
                    if not av or av["rt"] is None or bv["rt"] is None:
                        continue
                    if av["cost"] and bv["cost"]:
                        xs.append(bv["cost"] / av["cost"])   # estimated speedup
                        ys.append(bv["rt"] / av["rt"])        # actual speedup
                rho = spearman(xs, ys)
                print(f"{db:9s} {algo:9s} {rec['budget']:>7} {rho:>9.3f}  ({len(xs)})")


if __name__ == "__main__":
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
    for policy in ("impute", "drop"):
        analyze(policy)
    per_query("impute", budget)
    cost_fidelity("impute")
