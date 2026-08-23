"""Linearity analysis of estimated cost vs actual runtime across the 2x2
conditions (units x cards) in measurements.csv.

A perfectly honest cost model has est_cost = k * actual_ms for one global k:
  - Spearman rho: does cost even rank queries correctly?
  - Pearson r on log-log: proportionality quality across magnitudes
  - spread = max/min of (est_cost / actual_ms): 1.0 means perfectly linear;
    10 means the cost-per-ms exchange rate varies 10x between queries.
"""
import csv
import math
import os
from collections import defaultdict

from scipy import stats

import paths

HERE = os.path.dirname(os.path.abspath(__file__))

rows = list(csv.DictReader(open(paths.MEASUREMENTS)))
by_cond = defaultdict(list)
for r in rows:
    by_cond[(r["units"], r["cards"])].append(
        (r["query"], float(r["est_cost"]), float(r["actual_ms"])))

print(f"{'units':<11} {'cards':<9} {'n':>2}  {'spearman':>8}  {'pearson(log)':>12}  {'ratio spread':>12}")
for cond in sorted(by_cond):
    data = by_cond[cond]
    costs = [c for _, c, _ in data]
    times = [t for _, _, t in data]
    rho, _ = stats.spearmanr(costs, times)
    r, _ = stats.pearsonr([math.log(c) for c in costs], [math.log(t) for t in times])
    ratios = [c / t for c, t in zip(costs, times)]
    spread = max(ratios) / min(ratios)
    print(f"{cond[0]:<11} {cond[1]:<9} {len(data):>2}  {rho:8.3f}  {r:12.3f}  {spread:11.1f}x")

print("\nPer-query detail (cost/ms = implied exchange rate; constant = linear):")
queries = sorted({r["query"] for r in rows})
conds = [("default", "default"), ("default", "injected"),
         ("calibrated", "default"), ("calibrated", "injected")]
hdr = "query  " + "".join(f"{u[:3]}+{c[:3]:<12}" for u, c in conds)
print(hdr)
for q in queries:
    line = f"{q:<6} "
    for cond in conds:
        match = [d for d in by_cond[cond] if d[0] == q]
        if match:
            _, cost, ms = match[0]
            line += f"{cost/ms:>10.0f}/ms   "
        else:
            line += " " * 16
    print(line)

print("\nPlan flips (join order changed by injection):")
fp = {(r["query"], r["units"], r["cards"]): r["join_order"] for r in rows}
for q in queries:
    for units in ("default", "calibrated"):
        a = fp.get((q, units, "default"))
        b = fp.get((q, units, "injected"))
        if a and b and a != b:
            print(f"  {q} [{units} units]: FLIPPED")
