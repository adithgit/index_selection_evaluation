import pandas as pd
import json
import glob
import re

def parse_indexes(idx_str):
    if not idx_str or pd.isna(idx_str) or idx_str == '[]': return set()
    return set([tuple([c.strip().replace('C ', '') for c in m.split(',')]) for m in re.findall(r'I\((.*?)\)', str(idx_str))])

files = glob.glob('benchmark_results/*.csv')
raw_data = []

for f in files:
    try:
        df = pd.read_csv(f, sep=';')
        if 'algorithm name' not in df.columns: continue
    except: continue
    
    try: budget_idx = df.columns.tolist().index("parameters")
    except ValueError: budget_idx = 3 
        
    query_cols = [c for c in df.columns if str(c).endswith('.sql')]
        
    for _, row in df.iterrows():
        db = row['db system'].upper()
        run_date = row.get('date', '')
        memory_bytes = row.get('memory consumption', 0)
        
        try:
            params = json.loads(str(row.iloc[budget_idx]).replace("'", '"'))
            budget = float(params.get('budget_MB', 0))
        except: continue
        
        q_runtimes = {}
        for q_col in query_cols:
            val = row[q_col]
            if pd.notna(val) and isinstance(val, str):
                try:
                    q_data = json.loads(val)
                    runtimes = q_data.get('Runtimes', [])
                    if runtimes and len(runtimes) > 0 and runtimes[0] is not None:
                        q_runtimes[q_col] = float(runtimes[0])
                except: pass
        
        raw_data.append({
            'date': run_date,
            'db': db,
            'budget': budget,
            'utilized_mb': float(memory_bytes) / (1024.0 * 1024.0),
            'q_runtimes': q_runtimes
        })
        
df = pd.DataFrame(raw_data)
df = df.sort_values('date', ascending=False).groupby(['db', 'budget']).first().reset_index()

all_query_cols = set()
for r_dict in df['q_runtimes']: all_query_cols.update(r_dict.keys())

timed_out_queries = set()
for r_dict in df['q_runtimes']:
    for q in all_query_cols:
        if q not in r_dict:
            timed_out_queries.add(q)

safe_core = [q for q in all_query_cols if q not in timed_out_queries]
df['runtime_s'] = df['q_runtimes'].apply(lambda r_dict: sum(r_dict.get(q, 0) for q in safe_core) / 1000.0)

# Set budget=0 as the baseline
baselines = df[df['budget'] == 0].set_index('db')['runtime_s'].to_dict()

df['speedup'] = df.apply(lambda r: baselines.get(r['db'], 0) / r['runtime_s'] if r['runtime_s'] > 0 else 0, axis=1)

print("POSTGRES Speedups vs Utilized Budget:")
print("-" * 65)
for _, r in df[df['db'] == 'POSTGRES'].sort_values('budget').iterrows():
    print(f"Target Budget: {r['budget']:>6.0f} MB | Utilized: {r['utilized_mb']:>8.2f} MB | Speedup: {r['speedup']:.2f}x")
