import pandas as pd
import numpy as np
from scipy.stats import gmean, spearmanr, kendalltau
import ast
import warnings
warnings.filterwarnings('ignore')

db_names = ['postgres', 'db2']
algorithms = ['extend', 'db2advis']
budgets = [250, 500, 1000, 1750, 2500, 3000, 3500, 4000, 4500, 5000, 6250, 7500, 10000, 12500, 15000]

def parse_runtime(val):
    if isinstance(val, str) and '"Runtimes":' in val:
        try:
            d = ast.literal_eval(val.replace('null', 'None'))
            r = d.get('Runtimes', [None])[0]
            return float(r) if r is not None else None
        except: return None
    return None

def parse_cost(val):
    if isinstance(val, str) and '"Cost":' in val:
        try:
            d = ast.literal_eval(val.replace('null', 'None'))
            return float(d.get('Cost', 0))
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

queries = sorted(set(baselines_rt['postgres'].keys()) & set(baselines_rt['db2'].keys()))

# ====================================================================
# APPROACH 2: Find metrics where the RANKING of algorithms is preserved
# For each budget: rank the algorithms by the metric on PG, then on DB2
# If ranks always match, the metric is DB-agnostic for comparison
# ====================================================================
print("=" * 80)
print("APPROACH: Per-query Rank Stability")
print("For each query, does the RELATIVE ranking of algorithms stay the same")
print("across databases? (i.e., if Extend > db2advis on PG, is it also on DB2?)")
print("=" * 80)

per_query_data = {}
for b in budgets:
    rows = {}
    for alg in algorithms:
        for db in db_names:
            rows[(alg, db)] = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
    
    for q in queries:
        speedups = {}
        for alg in algorithms:
            for db in db_names:
                row = rows.get((alg, db))
                base = baselines_rt[db].get(q)
                if row is not None and base:
                    idx = parse_runtime(row.get(q, ''))
                    if not idx or idx <= 0: idx = base
                    speedups[(alg, db)] = base / idx
        
        if len(speedups) == 4:
            key = (b, q)
            per_query_data[key] = speedups

# Check: for each budget+query, does "extend vs db2advis" comparison agree across DBs?
agree_count = 0
disagree_count = 0
for (b, q), sp in per_query_data.items():
    ext_pg = sp[('extend', 'postgres')]
    adv_pg = sp[('db2advis', 'postgres')]
    ext_db2 = sp[('extend', 'db2')]
    adv_db2 = sp[('db2advis', 'db2')]
    
    pg_winner = 'extend' if ext_pg > adv_pg else 'db2advis'
    db2_winner = 'extend' if ext_db2 > adv_db2 else 'db2advis'
    
    if pg_winner == db2_winner:
        agree_count += 1
    else:
        disagree_count += 1

total = agree_count + disagree_count
print(f"\nRaw speedup ranking agreement: {agree_count}/{total} ({agree_count/total*100:.1f}%)")
print(f"Disagreements: {disagree_count}/{total} ({disagree_count/total*100:.1f}%)")

# ====================================================================
# APPROACH 3: Normalized Speedup Ratio
# For each query: compute extend_speedup / db2advis_speedup on EACH db
# If this ratio is stable across databases, that's the golden metric
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH: Algorithm Speedup Ratio Stability")
print("Ratio = Extend_Speedup / db2advis_Speedup for each query at each budget")
print("If ratio on PG ≈ ratio on DB2, we have a DB-agnostic comparison metric")
print("=" * 80)

ratios_pg = []
ratios_db2 = []
ratio_diffs = []
for (b, q), sp in per_query_data.items():
    r_pg = sp[('extend', 'postgres')] / sp[('db2advis', 'postgres')]
    r_db2 = sp[('extend', 'db2')] / sp[('db2advis', 'db2')]
    ratios_pg.append(r_pg)
    ratios_db2.append(r_db2)
    avg = (r_pg + r_db2) / 2
    if avg > 0.001:
        ratio_diffs.append(abs(r_pg - r_db2) / avg * 100)

corr, pval = spearmanr(ratios_pg, ratios_db2)
tau, tau_pval = kendalltau(ratios_pg, ratios_db2)
print(f"\nSpearman correlation of ratio across DBs: {corr:.4f} (p={pval:.2e})")
print(f"Kendall tau of ratio across DBs: {tau:.4f} (p={tau_pval:.2e})")
print(f"Mean Absolute Percentage Difference of ratio: {np.mean(ratio_diffs):.1f}%")
print(f"Median Absolute Percentage Difference of ratio: {np.median(ratio_diffs):.1f}%")

# ====================================================================
# APPROACH 4: GMS Ratio (the aggregate version)
# For each budget: compute GMS(extend)/GMS(db2advis) on PG vs DB2
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH: GMS Ratio (Aggregate)")
print("Ratio = GMS(Extend) / GMS(db2advis) at each budget")
print("=" * 80)

print(f"\n{'Budget':>8} | {'PG Ratio':>12} {'DB2 Ratio':>12} {'Diff%':>8}")
print('-' * 50)
gms_ratio_diffs = []
for b in budgets:
    gms_vals = {}
    for alg in algorithms:
        for db in db_names:
            row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            speedups = []
            for q in queries:
                base = baselines_rt[db].get(q)
                if row is not None and base:
                    idx = parse_runtime(row.get(q, ''))
                    if not idx or idx <= 0: idx = base
                    speedups.append(base / idx)
            if speedups:
                gms_vals[(alg, db)] = gmean(speedups)
    
    if len(gms_vals) == 4:
        r_pg = gms_vals[('extend', 'postgres')] / gms_vals[('db2advis', 'postgres')]
        r_db2 = gms_vals[('extend', 'db2')] / gms_vals[('db2advis', 'db2')]
        avg = (r_pg + r_db2) / 2
        diff = abs(r_pg - r_db2) / avg * 100
        gms_ratio_diffs.append(diff)
        print(f"{b:>8} | {r_pg:>12.4f} {r_db2:>12.4f} {diff:>7.1f}%")

print(f"\nMean MAPE of GMS Ratio: {np.mean(gms_ratio_diffs):.1f}%")

# ====================================================================
# APPROACH 5: Cost Reduction Ratio
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH: Cost Reduction Ratio (Aggregate)")
print("Ratio = CostReduction(Extend) / CostReduction(db2advis) at each budget")
print("=" * 80)

print(f"\n{'Budget':>8} | {'PG Ratio':>12} {'DB2 Ratio':>12} {'Diff%':>8}")
print('-' * 50)
cr_ratio_diffs = []
for b in budgets:
    cr_vals = {}
    for alg in algorithms:
        for db in db_names:
            row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            reductions = []
            for q in queries:
                base_co = baselines_cost[db].get(q)
                if row is not None and base_co:
                    idx_co = parse_cost(row.get(q, ''))
                    if idx_co and idx_co > 0:
                        reductions.append(base_co / idx_co)
            if reductions:
                cr_vals[(alg, db)] = gmean(reductions)
    
    if len(cr_vals) == 4:
        r_pg = cr_vals[('extend', 'postgres')] / cr_vals[('db2advis', 'postgres')]
        r_db2 = cr_vals[('extend', 'db2')] / cr_vals[('db2advis', 'db2')]
        avg = (r_pg + r_db2) / 2
        diff = abs(r_pg - r_db2) / avg * 100
        cr_ratio_diffs.append(diff)
        print(f"{b:>8} | {r_pg:>12.4f} {r_db2:>12.4f} {diff:>7.1f}%")

print(f"\nMean MAPE of Cost Reduction Ratio: {np.mean(cr_ratio_diffs):.1f}%")

# ====================================================================
# SUMMARY
# ====================================================================
print("\n" + "=" * 80)
print("FINAL SUMMARY: DB-Agnostic Metric Candidates")
print("=" * 80)
print(f"{'Metric':.<45} {'MAPE%':>8}")
print(f"{'GMS Ratio (Extend/db2advis)':.<45} {np.mean(gms_ratio_diffs):>8.1f}")
print(f"{'Cost Reduction Ratio (Extend/db2advis)':.<45} {np.mean(cr_ratio_diffs):>8.1f}")
print(f"{'Per-Query Speedup Ratio':.<45} {np.mean(ratio_diffs):>8.1f}")
print(f"{'Per-Query Ranking Agreement':.<45} {agree_count/total*100:>8.1f}  (% agreement)")
