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

# Compute per-query speedups for every combo
all_speedups = {}
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

# Load n_indexes from CSVs
def get_n_indexes(filepath, budget):
    try:
        df = pd.read_csv(filepath, sep=';')
        b_df = df[df['parameters'].str.contains(f'"budget_MB": {budget}') | df['parameters'].str.contains(f'"budget_MB":{budget}')]
        if b_df.empty: return 0
        return float(b_df.iloc[0].get('#indexes', 0))
    except: return 0

# ====================================================================
# Build time series for MANY candidate metrics
# For each (alg, db), we get a vector of 15 values (one per budget)
# Then check Spearman correlation between PG and DB2 vectors
# ====================================================================

def compute_metrics_at_budget(b, alg, db):
    sp = all_speedups.get((b, alg, db), {})
    if not sp: return {}
    vals = np.array(list(sp.values()))
    log_vals = np.log(vals[vals > 0])
    n_idx = get_n_indexes(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
    
    return {
        'GMS': gmean(vals),
        'Arithmetic Mean Speedup': np.mean(vals),
        'Median Speedup': np.median(vals),
        'Harmonic Mean Speedup': 1.0 / np.mean(1.0 / vals) if np.all(vals > 0) else 0,
        'P25 Speedup': np.percentile(vals, 25),
        'P75 Speedup': np.percentile(vals, 75),
        'IQR Speedup': np.percentile(vals, 75) - np.percentile(vals, 25),
        'Max Speedup': np.max(vals),
        'Min Speedup': np.min(vals),
        'Std Speedup': np.std(vals),
        '% Improved >10%': np.mean(vals > 1.1) * 100,
        '% Improved >50%': np.mean(vals > 1.5) * 100,
        '% Improved >2x': np.mean(vals > 2.0) * 100,
        'Total Time Reduction %': (1 - np.sum(1.0/vals) / len(vals)) * 100,
        '# Indexes Created': n_idx,
        'Log-Mean Speedup': np.exp(np.mean(log_vals)) if len(log_vals) > 0 else 1.0,
        'Trimmed Mean (10%)': np.mean(np.sort(vals)[3:-3]) if len(vals) > 6 else np.mean(vals),
        'CV (Coeff. of Variation)': np.std(vals) / np.mean(vals) if np.mean(vals) > 0 else 0,
        'Geometric Std': np.exp(np.std(log_vals)) if len(log_vals) > 0 else 1.0,
        'Sum of Speedups': np.sum(vals),
        'Speedup Entropy': -np.sum((vals/np.sum(vals)) * np.log(vals/np.sum(vals) + 1e-10)),
    }

# Build the full dataset
metric_names = None
series = {}  # (alg, db, metric_name) -> list of 15 values
for alg in algorithms:
    for db in db_names:
        for b in budgets:
            m = compute_metrics_at_budget(b, alg, db)
            if metric_names is None and m:
                metric_names = list(m.keys())
            for mn, val in m.items():
                key = (alg, db, mn)
                if key not in series: series[key] = []
                series[key].append(val)

# ====================================================================
# TREND ANALYSIS: For each metric, compute:
# 1. Spearman rank correlation of PG vs DB2 budget series (per algorithm)
# 2. Whether both curves are monotonically increasing/decreasing
# 3. Direction agreement at each budget step
# ====================================================================

print("=" * 100)
print("TREND SIMILARITY ANALYSIS")
print("For each metric, how similar is the SHAPE of the curve across databases?")
print("=" * 100)

results = []
for mn in metric_names:
    rhos = []
    direction_agreements = []
    monotone_matches = 0
    
    for alg in algorithms:
        pg_series = series.get((alg, 'postgres', mn), [])
        db2_series = series.get((alg, 'db2', mn), [])
        
        if len(pg_series) == len(db2_series) == len(budgets):
            # Spearman correlation of the two budget-indexed series
            rho, pval = spearmanr(pg_series, db2_series)
            rhos.append(rho)
            
            # Direction agreement: at each budget step, do both go up or both go down?
            agrees = 0
            total_steps = 0
            for i in range(1, len(budgets)):
                pg_dir = np.sign(pg_series[i] - pg_series[i-1])
                db2_dir = np.sign(db2_series[i] - db2_series[i-1])
                if pg_dir == db2_dir:
                    agrees += 1
                total_steps += 1
            direction_agreements.append(agrees / total_steps * 100)
            
            # Monotonicity check
            pg_mono = all(pg_series[i] >= pg_series[i-1] for i in range(1, len(pg_series)))
            db2_mono = all(db2_series[i] >= db2_series[i-1] for i in range(1, len(db2_series)))
            if pg_mono == db2_mono:
                monotone_matches += 1
    
    if rhos:
        results.append({
            'metric': mn,
            'mean_rho': np.mean(rhos),
            'min_rho': np.min(rhos),
            'extend_rho': rhos[0] if len(rhos) > 0 else 0,
            'db2advis_rho': rhos[1] if len(rhos) > 1 else 0,
            'direction_agree': np.mean(direction_agreements),
            'monotone_match': monotone_matches,
            # Combined score: weighted average of correlation and direction agreement
            'trend_score': np.mean(rhos) * 0.6 + np.mean(direction_agreements) / 100 * 0.4
        })

df_results = pd.DataFrame(results).sort_values('trend_score', ascending=False)

print(f"\n{'Metric':<30} {'Spearman ρ':>12} {'Ext ρ':>8} {'Adv ρ':>8} {'Dir Agree%':>12} {'Mono Match':>12} {'Score':>8}")
print('-' * 96)
for _, row in df_results.iterrows():
    verdict = "✅" if row['trend_score'] > 0.7 else ("⚠️" if row['trend_score'] > 0.5 else "❌")
    print(f"{verdict} {row['metric']:<28} {row['mean_rho']:>12.4f} {row['extend_rho']:>8.4f} {row['db2advis_rho']:>8.4f} {row['direction_agree']:>11.1f}% {row['monotone_match']:>12} {row['trend_score']:>8.3f}")

# ====================================================================
# Print the top 5 in detail
# ====================================================================
top5 = df_results.head(5)['metric'].tolist()
print(f"\n{'='*100}")
print("TOP 5 TREND-STABLE METRICS: Budget-by-Budget Values")
print(f"{'='*100}")

for mn in top5:
    print(f"\n--- {mn} ---")
    for alg in algorithms:
        pg_s = series.get((alg, 'postgres', mn), [])
        db2_s = series.get((alg, 'db2', mn), [])
        print(f"\n  {alg.upper()}:")
        print(f"  {'Budget':>8} | {'Postgres':>12} {'DB2':>12} | {'PG Δ':>8} {'DB2 Δ':>8} {'Same dir?':>10}")
        print(f"  {'-'*65}")
        for i, b in enumerate(budgets):
            pg_v = pg_s[i] if i < len(pg_s) else 0
            db2_v = db2_s[i] if i < len(db2_s) else 0
            if i > 0:
                pg_delta = pg_s[i] - pg_s[i-1]
                db2_delta = db2_s[i] - db2_s[i-1]
                same = "✅" if np.sign(pg_delta) == np.sign(db2_delta) else "❌"
                print(f"  {b:>8} | {pg_v:>12.4f} {db2_v:>12.4f} | {pg_delta:>+8.4f} {db2_delta:>+8.4f} {same:>10}")
            else:
                print(f"  {b:>8} | {pg_v:>12.4f} {db2_v:>12.4f} |")
