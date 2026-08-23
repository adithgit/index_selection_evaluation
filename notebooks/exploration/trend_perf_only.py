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

# Build per-query speedups
all_speedups = {}
all_indexed_rt = {}
for b in budgets:
    for alg in algorithms:
        for db in db_names:
            row = load_row(f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            sp = {}
            irt = {}
            for q in queries:
                base = baselines_rt[db].get(q)
                if base:
                    idx = parse_runtime(row.get(q, '')) if row is not None else None
                    if not idx or idx <= 0: idx = base
                    sp[q] = base / idx
                    irt[q] = idx
            all_speedups[(b, alg, db)] = sp
            all_indexed_rt[(b, alg, db)] = irt

# ====================================================================
# NEW PERFORMANCE METRICS
# ====================================================================

def compute_new_metrics(b, alg, db):
    sp = all_speedups.get((b, alg, db), {})
    irt = all_indexed_rt.get((b, alg, db), {})
    base = baselines_rt[db]
    if not sp: return {}
    
    speedups = np.array(list(sp.values()))
    base_times = np.array([base[q] for q in sp.keys()])
    idx_times = np.array([irt[q] for q in sp.keys()])
    log_speedups = np.log(speedups[speedups > 0])
    
    # 1. Workload Runtime Ratio: total_indexed / total_baseline
    #    Lower = better. This weights queries by their actual runtime.
    workload_ratio = np.sum(idx_times) / np.sum(base_times)
    
    # 2. Time Saved %: (total_base - total_indexed) / total_base * 100
    time_saved_pct = (1 - workload_ratio) * 100
    
    # 3. Runtime-Weighted GMS: weight each log(speedup) by baseline runtime
    weights = base_times / np.sum(base_times)
    weighted_log_gms = np.exp(np.sum(weights * log_speedups))
    
    # 4. Fraction of queries with ANY improvement (speedup > 1.0)
    frac_any_improvement = np.mean(speedups > 1.001) * 100
    
    # 5. Fraction of queries with MEANINGFUL improvement (speedup > 1.25)
    frac_meaningful = np.mean(speedups > 1.25) * 100
    
    # 6. Worst-case: minimum speedup (did any query get SLOWER?)
    worst_speedup = np.min(speedups)
    
    # 7. Ratio of improved to degraded queries
    improved = np.sum(speedups > 1.001)
    degraded = np.sum(speedups < 0.999)
    neutral = np.sum((speedups >= 0.999) & (speedups <= 1.001))
    improve_ratio = improved / max(degraded, 1)
    
    # 8. Average of log-speedups (= log of GMS, but additive scale)
    avg_log_speedup = np.mean(log_speedups)
    
    # 9. Weighted time saved: sum of (base_i - idx_i) for only improved queries
    time_saved_improved = sum(base[q] - irt[q] for q in sp.keys() if sp[q] > 1.001)
    pct_time_saved_improved = time_saved_improved / np.sum(base_times) * 100
    
    # 10. Median of log-speedups (robust central tendency)
    median_log_speedup = np.median(log_speedups)
    
    # 11. Winsorized mean (cap extreme values at 5th and 95th percentile)
    p5, p95 = np.percentile(speedups, [5, 95])
    winsorized = np.clip(speedups, p5, p95)
    winsorized_mean = np.mean(winsorized)
    
    # 12. Proportion of total time saved that came from top-3 queries
    time_savings = {q: base[q] - irt[q] for q in sp.keys()}
    top3_savings = sum(sorted(time_savings.values(), reverse=True)[:3])
    total_savings = sum(max(0, v) for v in time_savings.values())
    concentration = top3_savings / max(total_savings, 0.001) * 100
    
    return {
        'Workload Runtime Ratio': workload_ratio,
        'Time Saved %': time_saved_pct,
        'Runtime-Weighted GMS': weighted_log_gms,
        '% Any Improvement': frac_any_improvement,
        '% Meaningful Improvement (>25%)': frac_meaningful,
        'Worst-Case Speedup': worst_speedup,
        'Improved/Degraded Ratio': improve_ratio,
        'Avg Log-Speedup': avg_log_speedup,
        '% Time Saved (improved only)': pct_time_saved_improved,
        'Median Log-Speedup': median_log_speedup,
        'Winsorized Mean Speedup': winsorized_mean,
        'Top-3 Savings Concentration %': concentration,
        # Also include the old best performers for comparison
        'GMS': gmean(speedups),
        'Trimmed Mean (10%)': np.mean(np.sort(speedups)[3:-3]) if len(speedups) > 6 else np.mean(speedups),
    }

# Build series
metric_names = None
series = {}
for alg in algorithms:
    for db in db_names:
        for b in budgets:
            m = compute_new_metrics(b, alg, db)
            if metric_names is None and m:
                metric_names = list(m.keys())
            for mn, val in m.items():
                key = (alg, db, mn)
                if key not in series: series[key] = []
                series[key].append(val)

# Score each metric by trend similarity
print("=" * 105)
print("PERFORMANCE-ONLY TREND SIMILARITY (runtime-based, measures actual execution improvement)")
print("=" * 105)
print(f"\n{'Metric':<38} {'Mean ρ':>8} {'Ext ρ':>8} {'Adv ρ':>8} {'Dir%':>6} {'Score':>8}")
print('-' * 82)

results = []
for mn in metric_names:
    rhos = []
    dir_agrees = []
    for alg in algorithms:
        pg_s = series.get((alg, 'postgres', mn), [])
        db2_s = series.get((alg, 'db2', mn), [])
        if len(pg_s) == len(db2_s) == len(budgets):
            rho, _ = spearmanr(pg_s, db2_s)
            rhos.append(rho)
            agrees = sum(1 for i in range(1, len(budgets)) 
                        if np.sign(pg_s[i]-pg_s[i-1]) == np.sign(db2_s[i]-db2_s[i-1]))
            dir_agrees.append(agrees / (len(budgets)-1) * 100)
    
    if rhos:
        score = np.mean(rhos) * 0.6 + np.mean(dir_agrees) / 100 * 0.4
        verdict = "✅" if score > 0.65 else ("⚠️" if score > 0.5 else "❌")
        results.append((mn, np.mean(rhos), rhos[0], rhos[1], np.mean(dir_agrees), score, verdict))

results.sort(key=lambda x: -x[5])
for mn, mean_rho, ext_rho, adv_rho, dir_pct, score, verdict in results:
    print(f"{verdict} {mn:<36} {mean_rho:>8.4f} {ext_rho:>8.4f} {adv_rho:>8.4f} {dir_pct:>5.1f}% {score:>8.3f}")

# Print detailed view for top 3
print(f"\n{'='*105}")
print("TOP 3 PERFORMANCE METRICS - Budget-by-Budget")
print(f"{'='*105}")

for mn, _, _, _, _, score, _ in results[:3]:
    print(f"\n{'─'*80}")
    print(f"  {mn} (Score: {score:.3f})")
    print(f"{'─'*80}")
    for alg in algorithms:
        pg_s = series.get((alg, 'postgres', mn), [])
        db2_s = series.get((alg, 'db2', mn), [])
        print(f"\n  {alg.upper()}:")
        print(f"  {'Budget':>8} | {'Postgres':>12} {'DB2':>12} | {'PG trend':>10} {'DB2 trend':>10} {'Match':>6}")
        print(f"  {'-'*68}")
        for i, b in enumerate(budgets):
            if i > 0:
                pg_dir = "↑" if pg_s[i] > pg_s[i-1] else ("↓" if pg_s[i] < pg_s[i-1] else "→")
                db2_dir = "↑" if db2_s[i] > db2_s[i-1] else ("↓" if db2_s[i] < db2_s[i-1] else "→")
                match = "✅" if pg_dir == db2_dir else "❌"
                print(f"  {b:>8} | {pg_s[i]:>12.4f} {db2_s[i]:>12.4f} | {pg_dir:>10} {db2_dir:>10} {match:>6}")
            else:
                print(f"  {b:>8} | {pg_s[i]:>12.4f} {db2_s[i]:>12.4f} |")
