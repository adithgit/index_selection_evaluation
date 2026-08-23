import pandas as pd
import numpy as np
from scipy.stats import gmean, spearmanr
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

def load_row(filepath, budget=None):
    try:
        df = pd.read_csv(filepath, sep=';')
        if budget is not None:
            df = df[df['parameters'].str.contains(f'"budget_MB": {budget}') | df['parameters'].str.contains(f'"budget_MB":{budget}')]
        if df.empty: return None
        return df.iloc[0]
    except: return None

baselines_rt = {}
for db in db_names:
    row = load_row(f'benchmark_results/results_no_index_JOB_REFINED_{db}_33_queries.csv')
    if row is not None:
        baselines_rt[db] = {}
        for col in row.index:
            if col.endswith('.sql'):
                rt = parse_runtime(row[col])
                if rt and rt > 0: baselines_rt[db][col] = rt

queries = sorted(set(baselines_rt['postgres'].keys()) & set(baselines_rt['db2'].keys()))
print(f"Common queries: {len(queries)}")

# ====================================================================
# For each (budget, alg, db), compute per-query speedups
# ====================================================================
all_speedups = {}  # (budget, alg, db) -> {query: speedup}
for b in budgets:
    for alg in algorithms:
        for db in db_names:
            row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            sp = {}
            for q in queries:
                base = baselines_rt[db].get(q)
                if base:
                    idx = parse_runtime(row.get(q, '')) if row is not None else None
                    if not idx or idx <= 0: idx = base
                    sp[q] = base / idx
            all_speedups[(b, alg, db)] = sp

# ====================================================================
# APPROACH A: For each budget, compute Spearman rank correlation of 
# per-query speedups between PG and DB2 for the SAME algorithm
# If high, the algorithm ranks queries consistently across DBs
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH A: Per-query Speedup Rank Correlation (same alg, PG vs DB2)")
print("Does each algorithm rank queries the same way on both databases?")
print("=" * 80)

for alg in algorithms:
    corrs = []
    for b in budgets:
        sp_pg = all_speedups[(b, alg, 'postgres')]
        sp_db2 = all_speedups[(b, alg, 'db2')]
        common = sorted(set(sp_pg.keys()) & set(sp_db2.keys()))
        if len(common) > 5:
            pg_vals = [sp_pg[q] for q in common]
            db2_vals = [sp_db2[q] for q in common]
            rho, _ = spearmanr(pg_vals, db2_vals)
            corrs.append(rho)
    print(f"  {alg}: Mean Spearman ρ = {np.mean(corrs):.4f}, Min = {np.min(corrs):.4f}, Max = {np.max(corrs):.4f}")

# ====================================================================
# APPROACH B: Normalize speedups WITHIN each database
# For each (budget, db), divide each query's speedup by the max speedup
# This gives a 0-1 "efficiency score" that strips out DB baseline effects
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH B: Normalized Efficiency Score")
print("For each (budget, db): normalize speedup_q / max_speedup")
print("Then compare the GeoMean of normalized scores across DBs")
print("=" * 80)

norm_data = []
for b in budgets:
    for alg in algorithms:
        for db in db_names:
            sp = all_speedups[(b, alg, db)]
            if not sp: continue
            vals = list(sp.values())
            max_sp = max(vals)
            if max_sp > 0:
                normalized = [v / max_sp for v in vals]
                norm_data.append({
                    'budget': b, 'alg': alg, 'db': db,
                    'gm_normalized': gmean(normalized),
                    'mean_normalized': np.mean(normalized),
                    'median_normalized': np.median(normalized)
                })

df_norm = pd.DataFrame(norm_data)

for metric in ['gm_normalized', 'mean_normalized', 'median_normalized']:
    diffs_by_alg = {}
    for alg in algorithms:
        diffs = []
        for b in budgets:
            pg = df_norm[(df_norm['alg']==alg) & (df_norm['db']=='postgres') & (df_norm['budget']==b)][metric].values
            db2 = df_norm[(df_norm['alg']==alg) & (df_norm['db']=='db2') & (df_norm['budget']==b)][metric].values
            if len(pg) > 0 and len(db2) > 0:
                avg = (pg[0] + db2[0]) / 2
                if avg > 0.001:
                    diffs.append(abs(pg[0] - db2[0]) / avg * 100)
        diffs_by_alg[alg] = np.mean(diffs) if diffs else 999
    
    # Also compute ratio stability
    ratio_diffs = []
    for b in budgets:
        vals = {}
        for alg in algorithms:
            for db in db_names:
                v = df_norm[(df_norm['alg']==alg) & (df_norm['db']==db) & (df_norm['budget']==b)][metric].values
                if len(v) > 0: vals[(alg, db)] = v[0]
        if len(vals) == 4 and vals[('db2advis','postgres')] > 0.001 and vals[('db2advis','db2')] > 0.001:
            r_pg = vals[('extend','postgres')] / vals[('db2advis','postgres')]
            r_db2 = vals[('extend','db2')] / vals[('db2advis','db2')]
            avg = (abs(r_pg) + abs(r_db2)) / 2
            if avg > 0.001:
                ratio_diffs.append(abs(r_pg - r_db2) / avg * 100)
    
    print(f"\n  {metric}:")
    print(f"    Per-alg MAPE (extend): {diffs_by_alg['extend']:.1f}%, (db2advis): {diffs_by_alg['db2advis']:.1f}%")
    print(f"    Ratio MAPE (Ext/Adv across DBs): {np.mean(ratio_diffs):.1f}%")

# ====================================================================
# APPROACH C: Relative Performance Index (RPI)
# For each query at each budget: compute speedup_extend / speedup_db2advis
# on EACH database. If median of these ratios is stable, we have our metric.
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH C: Relative Performance Index (per-query alg comparison)")
print("For each query: ratio = speedup(extend) / speedup(db2advis)")
print("Then aggregate these ratios per budget")
print("=" * 80)

print(f"\n{'Budget':>8} | {'GM Ratio PG':>12} {'GM Ratio DB2':>12} {'Diff%':>8} | {'Med Ratio PG':>13} {'Med Ratio DB2':>14} {'Diff%':>8}")
print('-' * 95)
gm_diffs = []
med_diffs = []
for b in budgets:
    sp_ext_pg = all_speedups[(b, 'extend', 'postgres')]
    sp_ext_db2 = all_speedups[(b, 'extend', 'db2')]
    sp_adv_pg = all_speedups[(b, 'db2advis', 'postgres')]
    sp_adv_db2 = all_speedups[(b, 'db2advis', 'db2')]
    
    ratios_pg = []
    ratios_db2 = []
    for q in queries:
        if q in sp_ext_pg and q in sp_adv_pg and sp_adv_pg[q] > 0.001:
            ratios_pg.append(sp_ext_pg[q] / sp_adv_pg[q])
        if q in sp_ext_db2 and q in sp_adv_db2 and sp_adv_db2[q] > 0.001:
            ratios_db2.append(sp_ext_db2[q] / sp_adv_db2[q])
    
    if ratios_pg and ratios_db2:
        gm_pg = gmean(ratios_pg)
        gm_db2 = gmean(ratios_db2)
        med_pg = np.median(ratios_pg)
        med_db2 = np.median(ratios_db2)
        
        gm_avg = (gm_pg + gm_db2) / 2
        gm_diff = abs(gm_pg - gm_db2) / gm_avg * 100
        gm_diffs.append(gm_diff)
        
        med_avg = (med_pg + med_db2) / 2
        med_diff = abs(med_pg - med_db2) / med_avg * 100
        med_diffs.append(med_diff)
        
        print(f"{b:>8} | {gm_pg:>12.4f} {gm_db2:>12.4f} {gm_diff:>7.1f}% | {med_pg:>13.4f} {med_db2:>14.4f} {med_diff:>7.1f}%")

print(f"\n  GM of per-query ratios: Mean MAPE = {np.mean(gm_diffs):.1f}%")
print(f"  Median of per-query ratios: Mean MAPE = {np.mean(med_diffs):.1f}%")

# ====================================================================
# APPROACH D: Query Win Rate
# For each budget: what % of queries does Extend beat db2advis?
# ====================================================================
print("\n" + "=" * 80)
print("APPROACH D: Query Win Rate (% of queries where Extend > db2advis)")
print("=" * 80)

print(f"\n{'Budget':>8} | {'Win% PG':>10} {'Win% DB2':>10} {'Diff%':>8}")
print('-' * 45)
wr_diffs = []
for b in budgets:
    sp_ext_pg = all_speedups[(b, 'extend', 'postgres')]
    sp_ext_db2 = all_speedups[(b, 'extend', 'db2')]
    sp_adv_pg = all_speedups[(b, 'db2advis', 'postgres')]
    sp_adv_db2 = all_speedups[(b, 'db2advis', 'db2')]
    
    wins_pg = sum(1 for q in queries if sp_ext_pg.get(q, 0) > sp_adv_pg.get(q, 0))
    wins_db2 = sum(1 for q in queries if sp_ext_db2.get(q, 0) > sp_adv_db2.get(q, 0))
    total = len(queries)
    
    wr_pg = wins_pg / total * 100
    wr_db2 = wins_db2 / total * 100
    avg = (wr_pg + wr_db2) / 2
    diff = abs(wr_pg - wr_db2) / avg * 100 if avg > 0 else 0
    wr_diffs.append(diff)
    
    print(f"{b:>8} | {wr_pg:>9.1f}% {wr_db2:>9.1f}% {diff:>7.1f}%")

print(f"\n  Win Rate Mean MAPE: {np.mean(wr_diffs):.1f}%")

# ====================================================================
# FINAL RANKING (runtime-only)
# ====================================================================
print("\n" + "=" * 80)
print("FINAL RANKING: Runtime-Only DB-Agnostic Metrics")
print("(Lower MAPE = more stable across databases)")
print("=" * 80)
candidates = [
    ("Query Win Rate", np.mean(wr_diffs)),
    ("Median of Per-Query Ratios", np.mean(med_diffs)),
    ("GM of Per-Query Ratios (= GMS Ratio)", np.mean(gm_diffs)),
]
candidates.sort(key=lambda x: x[1])
for name, mape in candidates:
    verdict = "✅" if mape < 20 else ("⚠️" if mape < 40 else "❌")
    print(f"  {verdict} {name:<45} MAPE = {mape:.1f}%")
