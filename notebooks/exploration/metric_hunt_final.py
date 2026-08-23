import pandas as pd
import numpy as np
from scipy.stats import gmean
import ast
import warnings
warnings.filterwarnings('ignore')

db_names = ['postgres', 'db2']
algorithms = ['extend', 'db2advis']
budgets = [250, 500, 1000, 1750, 2500, 3000, 3500, 4000, 4500, 5000, 6250, 7500, 10000, 12500, 15000]

def parse_cost(val):
    if isinstance(val, str) and '"Cost":' in val:
        try:
            d = ast.literal_eval(val.replace('null', 'None'))
            return float(d.get('Cost', 0))
        except: return None
    return None

def parse_runtime(val):
    if isinstance(val, str) and '"Runtimes":' in val:
        try:
            d = ast.literal_eval(val.replace('null', 'None'))
            r = d.get('Runtimes', [None])[0]
            return float(r) if r is not None else None
        except: return None
    return None

def load_row(filepath, budget=None):
    try:
        df = pd.read_csv(filepath, sep=';')
        if budget is not None:
            df = df[df['parameters'].str.contains(f'"budget_MB": {budget}') | df['parameters'].str.contains(f'"budget_MB":{budget}')]
        if df.empty: return None
        return df.iloc[0]
    except: return None

baselines_rt = {}
baselines_cost = {}
for db in db_names:
    row = load_row(f'benchmark_results/results_no_index_JOB_REFINED_{db}_33_queries.csv')
    if row is not None:
        baselines_rt[db] = {}
        baselines_cost[db] = {}
        for col in row.index:
            if col.endswith('.sql'):
                rt = parse_runtime(row[col])
                co = parse_cost(row[col])
                if rt and rt > 0: baselines_rt[db][col] = rt
                if co and co > 0: baselines_cost[db][col] = co

queries_rt = sorted(set(baselines_rt.get('postgres', {}).keys()) & set(baselines_rt.get('db2', {}).keys()))
queries_cost = sorted(set(baselines_cost.get('postgres', {}).keys()) & set(baselines_cost.get('db2', {}).keys()))

# ====================================================================
# THE REAL QUESTION: We need a metric M such that
#   M(extend, postgres) / M(db2advis, postgres) ≈ M(extend, db2) / M(db2advis, db2)
# This allows you to say "extend is X% better than db2advis" regardless of DB.
# ====================================================================

print("=" * 100)
print("EXHAUSTIVE SEARCH: Ratio M(extend)/M(db2advis) stability across databases")
print("Goal: Find metric where this ratio is IDENTICAL on Postgres and DB2")
print("=" * 100)

def compute_all_metrics(alg, db, budget):
    """Returns a dict of metric_name -> value"""
    row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', budget)
    if row is None: return {}
    
    speedups = []
    cost_reds = []
    for q in queries_rt:
        base = baselines_rt[db].get(q)
        if base:
            idx = parse_runtime(row.get(q, ''))
            if not idx or idx <= 0: idx = base
            speedups.append(base / idx)
    
    for q in queries_cost:
        base_co = baselines_cost[db].get(q)
        if base_co:
            idx_co = parse_cost(row.get(q, ''))
            if idx_co and idx_co > 0:
                cost_reds.append(base_co / idx_co)
    
    if not speedups: return {}
    speedups = np.array(speedups)
    cost_reds = np.array(cost_reds) if cost_reds else np.array([1.0])
    log_speedups = np.log(speedups[speedups > 0])
    
    n_idx = float(row.get('#indexes', 0))
    
    return {
        'GMS': gmean(speedups),
        'Arithmetic Mean Speedup': np.mean(speedups),
        'Median Speedup': np.median(speedups),
        'Harmonic Mean Speedup': 1.0 / np.mean(1.0 / speedups),
        'Log-Mean Speedup': np.exp(np.mean(log_speedups)),  # equivalent to GMS
        'Trimmed Mean Speedup (10%)': np.mean(np.sort(speedups)[3:-3]) if len(speedups) > 6 else np.mean(speedups),
        'P25 Speedup': np.percentile(speedups, 25),
        'P75 Speedup': np.percentile(speedups, 75),
        'Std Speedup': np.std(speedups),
        'Max Speedup': np.max(speedups),
        '% Queries Improved >10%': np.mean(speedups > 1.1) * 100,
        '% Queries Improved >50%': np.mean(speedups > 1.5) * 100,
        '% Queries Improved >2x': np.mean(speedups > 2.0) * 100,
        'GM Cost Reduction': gmean(cost_reds),
        'Mean Cost Reduction': np.mean(cost_reds),
        'Median Cost Reduction': np.median(cost_reds),
        'Total Time Reduction %': (1 - 1.0/np.mean(speedups)) * 100,
        '# Indexes': n_idx,
        'Speedup/Index (GMS)': gmean(speedups) / max(n_idx, 1),
        'Sum of Log Speedups': np.sum(log_speedups),
        'Geometric Std of Speedups': np.exp(np.std(log_speedups)),
        'Coefficient of Variation': np.std(speedups) / np.mean(speedups) if np.mean(speedups) > 0 else 0,
        'Entropy of Speedups': -np.sum((speedups/np.sum(speedups)) * np.log(speedups/np.sum(speedups) + 1e-10)),
        'L2 Norm of Speedups': np.sqrt(np.sum(speedups**2)),
    }

# For each metric, compute the ratio extend/db2advis on both DBs at each budget
metric_names = None
results = {}
for b in budgets:
    metrics = {}
    for alg in algorithms:
        for db in db_names:
            m = compute_all_metrics(alg, db, b)
            metrics[(alg, db)] = m
            if metric_names is None and m:
                metric_names = list(m.keys())
    results[b] = metrics

# Score each metric
print(f"\n{'Metric':<35} {'Mean MAPE%':>12} {'Max MAPE%':>12} {'Verdict':>20}")
print('-' * 85)

best_metric = None
best_mape = 999
all_scores = []

for mn in metric_names:
    diffs = []
    for b in budgets:
        m = results[b]
        ext_pg = m.get(('extend','postgres'), {}).get(mn)
        ext_db2 = m.get(('extend','db2'), {}).get(mn)
        adv_pg = m.get(('db2advis','postgres'), {}).get(mn)
        adv_db2 = m.get(('db2advis','db2'), {}).get(mn)
        
        if ext_pg and ext_db2 and adv_pg and adv_db2 and adv_pg != 0 and adv_db2 != 0:
            ratio_pg = ext_pg / adv_pg
            ratio_db2 = ext_db2 / adv_db2
            avg = (abs(ratio_pg) + abs(ratio_db2)) / 2
            if avg > 0.001:
                diffs.append(abs(ratio_pg - ratio_db2) / avg * 100)
    
    if diffs:
        mean_mape = np.mean(diffs)
        max_mape = np.max(diffs)
        verdict = "✅ EXCELLENT" if mean_mape < 15 else ("⚠️  GOOD" if mean_mape < 30 else "❌ POOR")
        print(f"{mn:<35} {mean_mape:>12.1f} {max_mape:>12.1f} {verdict:>20}")
        all_scores.append((mn, mean_mape, max_mape))
        if mean_mape < best_mape:
            best_mape = mean_mape
            best_metric = mn

print(f"\n{'='*85}")
print(f"🏆 WINNER: {best_metric} (Mean MAPE = {best_mape:.1f}%)")
print(f"{'='*85}")

# Print the winner in detail
print(f"\nDetailed budget-by-budget for: {best_metric}")
print(f"{'Budget':>8} | {'Ratio on PG':>14} {'Ratio on DB2':>14} {'Diff%':>8}")
print('-' * 50)
for b in budgets:
    m = results[b]
    ext_pg = m.get(('extend','postgres'), {}).get(best_metric, 0)
    ext_db2 = m.get(('extend','db2'), {}).get(best_metric, 0)
    adv_pg = m.get(('db2advis','postgres'), {}).get(best_metric, 0)
    adv_db2 = m.get(('db2advis','db2'), {}).get(best_metric, 0)
    if adv_pg and adv_db2:
        r_pg = ext_pg / adv_pg
        r_db2 = ext_db2 / adv_db2
        avg = (abs(r_pg) + abs(r_db2)) / 2
        diff = abs(r_pg - r_db2) / avg * 100 if avg > 0.001 else 0
        print(f"{b:>8} | {r_pg:>14.4f} {r_db2:>14.4f} {diff:>7.1f}%")

# Also print the raw values for context
print(f"\nRaw values of {best_metric}:")
print(f"{'Budget':>8} | {'Ext PG':>12} {'Ext DB2':>12} | {'Adv PG':>12} {'Adv DB2':>12}")
print('-' * 70)
for b in budgets:
    m = results[b]
    print(f"{b:>8} | {m.get(('extend','postgres'), {}).get(best_metric, 0):>12.4f} {m.get(('extend','db2'), {}).get(best_metric, 0):>12.4f} | {m.get(('db2advis','postgres'), {}).get(best_metric, 0):>12.4f} {m.get(('db2advis','db2'), {}).get(best_metric, 0):>12.4f}")
