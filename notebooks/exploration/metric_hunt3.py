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

def load_row(filepath, budget=None):
    try:
        df = pd.read_csv(filepath, sep=';')
        if budget is not None:
            df = df[df['parameters'].str.contains(f'"budget_MB": {budget}') | df['parameters'].str.contains(f'"budget_MB":{budget}')]
        if df.empty: return None
        return df.iloc[0]
    except: return None

baselines_cost = {}
for db in db_names:
    row = load_row(f'benchmark_results/results_no_index_JOB_REFINED_{db}_33_queries.csv')
    if row is not None:
        baselines_cost[db] = {}
        for col in row.index:
            if col.endswith('.sql'):
                co = parse_cost(row[col])
                if co and co > 0: baselines_cost[db][col] = co

queries = sorted(set(baselines_cost.get('postgres', {}).keys()) & set(baselines_cost.get('db2', {}).keys()))
print(f"Common queries with cost data: {len(queries)}")

# Print the raw cost reduction values per algorithm per database
print("\n" + "=" * 80)
print("GEOMETRIC MEAN COST REDUCTION (raw values)")
print("=" * 80)
print(f"\n{'Budget':>8} | {'Ext PG':>10} {'Ext DB2':>10} {'Diff%':>8} | {'Adv PG':>10} {'Adv DB2':>10} {'Diff%':>8}")
print('-' * 80)

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
        ext_pg = cr_vals[('extend', 'postgres')]
        ext_db2 = cr_vals[('extend', 'db2')]
        adv_pg = cr_vals[('db2advis', 'postgres')]
        adv_db2 = cr_vals[('db2advis', 'db2')]
        
        ext_avg = (ext_pg + ext_db2) / 2
        adv_avg = (adv_pg + adv_db2) / 2
        ext_diff = abs(ext_pg - ext_db2) / ext_avg * 100
        adv_diff = abs(adv_pg - adv_db2) / adv_avg * 100
        
        print(f"{b:>8} | {ext_pg:>10.4f} {ext_db2:>10.4f} {ext_diff:>7.1f}% | {adv_pg:>10.4f} {adv_db2:>10.4f} {adv_diff:>7.1f}%")

# Now try: Exclude budget 1750 outlier and recalculate MAPE
print("\n" + "=" * 80)
print("COST REDUCTION RATIO (excluding budget=1750 outlier)")
print("=" * 80)
cr_ratio_diffs_clean = []
for b in budgets:
    if b == 1750: continue
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
        cr_ratio_diffs_clean.append(diff)
        print(f"Budget {b:>6}: PG ratio={r_pg:.4f}, DB2 ratio={r_db2:.4f}, diff={diff:.1f}%")

print(f"\nMean MAPE (excl 1750): {np.mean(cr_ratio_diffs_clean):.1f}%")

# The winner: check if GM Cost Reduction is similar for EACH algorithm across DBs
print("\n" + "=" * 80)
print("FINAL CHECK: Is GM Cost Reduction itself stable per-algorithm across DBs?")
print("=" * 80)
for alg in algorithms:
    diffs = []
    for b in budgets:
        cr_vals = {}
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
                cr_vals[db] = gmean(reductions)
        if len(cr_vals) == 2:
            avg = (cr_vals['postgres'] + cr_vals['db2']) / 2
            d = abs(cr_vals['postgres'] - cr_vals['db2']) / avg * 100
            diffs.append(d)
    print(f"  {alg}: Mean MAPE across DBs = {np.mean(diffs):.1f}%")
