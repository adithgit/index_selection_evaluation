import pandas as pd
import numpy as np
from scipy.stats import gmean, hmean, spearmanr
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

# Load baselines
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
print(f"Common queries: {len(queries)}")

# Compute ALL derived metrics for every (budget, alg, db) combination
all_data = []
for b in budgets:
    for alg in algorithms:
        for db in db_names:
            row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            if row is None: continue
            
            speedups = []
            cost_reductions = []
            runtimes_indexed = []
            runtimes_base = []
            
            for q in queries:
                base_rt = baselines_rt[db].get(q)
                base_co = baselines_cost[db].get(q)
                if not base_rt: continue
                
                idx_rt = parse_runtime(row.get(q, ''))
                idx_co = parse_cost(row.get(q, ''))
                
                if not idx_rt or idx_rt <= 0: idx_rt = base_rt
                
                speedups.append(base_rt / idx_rt)
                runtimes_indexed.append(idx_rt)
                runtimes_base.append(base_rt)
                
                if base_co and idx_co and base_co > 0:
                    cost_reductions.append(base_co / idx_co)
            
            if not speedups: continue
            
            speedups = np.array(speedups)
            runtimes_indexed = np.array(runtimes_indexed)
            runtimes_base = np.array(runtimes_base)
            cost_reductions = np.array(cost_reductions) if cost_reductions else np.array([1.0])
            
            n_indexes = float(row.get('#indexes', 0))
            
            entry = {
                'budget': b, 'alg': alg, 'db': db,
                # --- Raw aggregate metrics ---
                'gms': gmean(speedups),
                'arithmetic_mean_speedup': np.mean(speedups),
                'median_speedup': np.median(speedups),
                'harmonic_mean_speedup': hmean(speedups[speedups > 0]),
                'max_speedup': np.max(speedups),
                'min_speedup': np.min(speedups),
                'std_speedup': np.std(speedups),
                'p25_speedup': np.percentile(speedups, 25),
                'p75_speedup': np.percentile(speedups, 75),
                'iqr_speedup': np.percentile(speedups, 75) - np.percentile(speedups, 25),
                # --- Cost-based metrics ---
                'gm_cost_reduction': gmean(cost_reductions),
                'mean_cost_reduction': np.mean(cost_reductions),
                'median_cost_reduction': np.median(cost_reductions),
                # --- Proportion metrics ---
                'pct_improved_10': np.mean(speedups > 1.10) * 100,
                'pct_improved_50': np.mean(speedups > 1.50) * 100,
                'pct_improved_200': np.mean(speedups > 2.0) * 100,
                # --- Normalized metrics ---
                'total_time_reduction_pct': (1 - np.sum(runtimes_indexed) / np.sum(runtimes_base)) * 100,
                'log_gms': np.log(gmean(speedups)),
                # --- Index efficiency ---
                'n_indexes': n_indexes,
                'speedup_per_index': gmean(speedups) / max(n_indexes, 1),
            }
            all_data.append(entry)

df = pd.DataFrame(all_data)

# Now find which metric has the SMALLEST relative difference between postgres and db2
# for BOTH algorithms
metric_cols = [c for c in df.columns if c not in ['budget', 'alg', 'db']]

print(f"\n{'='*80}")
print(f"METRIC STABILITY ANALYSIS: Which metric is most DB-agnostic?")
print(f"{'='*80}")
print(f"\nFor each metric, we compute the Mean Absolute Percentage Error (MAPE)")
print(f"between Postgres and DB2 values. Lower = more stable across databases.\n")

scores = []
for m in metric_cols:
    mapes = []
    for alg in algorithms:
        for b in budgets:
            pg_val = df[(df['alg']==alg) & (df['db']=='postgres') & (df['budget']==b)][m].values
            db2_val = df[(df['alg']==alg) & (df['db']=='db2') & (df['budget']==b)][m].values
            if len(pg_val) > 0 and len(db2_val) > 0:
                avg = (abs(pg_val[0]) + abs(db2_val[0])) / 2
                if avg > 0.001:
                    mapes.append(abs(pg_val[0] - db2_val[0]) / avg * 100)
    
    if mapes:
        overall_mape = np.mean(mapes)
        # Also split by algorithm
        mapes_extend = []
        mapes_db2advis = []
        for b in budgets:
            for alg, lst in [('extend', mapes_extend), ('db2advis', mapes_db2advis)]:
                pg_val = df[(df['alg']==alg) & (df['db']=='postgres') & (df['budget']==b)][m].values
                db2_val = df[(df['alg']==alg) & (df['db']=='db2') & (df['budget']==b)][m].values
                if len(pg_val) > 0 and len(db2_val) > 0:
                    avg = (abs(pg_val[0]) + abs(db2_val[0])) / 2
                    if avg > 0.001:
                        lst.append(abs(pg_val[0] - db2_val[0]) / avg * 100)
        
        scores.append({
            'metric': m,
            'overall_mape': overall_mape,
            'extend_mape': np.mean(mapes_extend) if mapes_extend else 999,
            'db2advis_mape': np.mean(mapes_db2advis) if mapes_db2advis else 999,
            'max_mape': max(np.max(mapes_extend) if mapes_extend else 999, np.max(mapes_db2advis) if mapes_db2advis else 999)
        })

scores_df = pd.DataFrame(scores).sort_values('overall_mape')
print(f"{'Metric':<30} {'Overall MAPE%':>14} {'Extend MAPE%':>14} {'db2advis MAPE%':>16} {'Max MAPE%':>12}")
print('-' * 90)
for _, row in scores_df.iterrows():
    print(f"{row['metric']:<30} {row['overall_mape']:>14.2f} {row['extend_mape']:>14.2f} {row['db2advis_mape']:>16.2f} {row['max_mape']:>12.2f}")

# Print the top 3 candidates in detail
print(f"\n{'='*80}")
print("TOP 3 CANDIDATES - Detailed Budget-by-Budget Values")
print(f"{'='*80}")
top3 = scores_df.head(3)['metric'].tolist()
for m in top3:
    print(f"\n--- {m} ---")
    print(f"{'Budget':>8} | {'Extend PG':>12} {'Extend DB2':>12} {'Diff%':>8} | {'db2advis PG':>12} {'db2advis DB2':>12} {'Diff%':>8}")
    print('-' * 90)
    for b in budgets:
        vals = {}
        for alg in algorithms:
            for db in db_names:
                v = df[(df['alg']==alg) & (df['db']==db) & (df['budget']==b)][m].values
                vals[(alg, db)] = v[0] if len(v) > 0 else float('nan')
        
        ext_pg = vals[('extend','postgres')]
        ext_db2 = vals[('extend','db2')]
        adv_pg = vals[('db2advis','postgres')]
        adv_db2 = vals[('db2advis','db2')]
        
        ext_avg = (abs(ext_pg)+abs(ext_db2))/2
        adv_avg = (abs(adv_pg)+abs(adv_db2))/2
        ext_diff = abs(ext_pg-ext_db2)/ext_avg*100 if ext_avg > 0.001 else 0
        adv_diff = abs(adv_pg-adv_db2)/adv_avg*100 if adv_avg > 0.001 else 0
        
        print(f"{b:>8} | {ext_pg:>12.4f} {ext_db2:>12.4f} {ext_diff:>7.1f}% | {adv_pg:>12.4f} {adv_db2:>12.4f} {adv_diff:>7.1f}%")
