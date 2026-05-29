# Colab Analysis Instructions

I have created an archive containing all your benchmark results so you can easily upload them to Google Colab and run the visualization code.

## Step 1: Download the Data
In your terminal or file explorer, locate this file and download it to your local machine:
`index_selection_evaluation/benchmark_results_archive.zip`

## Step 2: Set up Google Colab
1. Go to [Google Colab](https://colab.research.google.com/) and create a **New Notebook**.
2. Click the **Folder icon** on the left sidebar.
3. Click the **Upload icon** and upload the `benchmark_results_archive.zip` file you just downloaded.
4. Add a new code cell and run this command to unzip the data:
   ```bash
   !unzip -o benchmark_results_archive.zip -d benchmark_results
   ```

## Step 3: Run the Analysis Code
Copy the following Python code into a new cell and run it. The colors are perfectly matched, and the 0-budget baseline is explicitly included as the starting point of the line graphs.

```python
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import json
import glob
import re

# Set seaborn style for beautiful charts
sns.set_theme(style="whitegrid")
plt.rcParams['figure.figsize'] = (12, 6)

DB_COLORS = {'POSTGRES': '#1f77b4', 'DB2': '#ff7f0e'}

def parse_indexes(idx_str):
    """Extracts column tuples from the index string"""
    indexes = []
    if not idx_str or pd.isna(idx_str) or idx_str == '[]':
        return set()
    matches = re.findall(r'I\((.*?)\)', str(idx_str))
    for m in matches:
        cols = tuple([c.strip().replace('C ', '') for c in m.split(',')])
        indexes.append(cols)
    return set(indexes)

def load_data():
    files = glob.glob('**/*.csv', recursive=True)
    if not files:
        raise FileNotFoundError("Could not find any CSV files! Please make sure the zip file was uploaded and unzipped properly.")
        
    raw_data = []
    
    for f in files:
        try:
            df = pd.read_csv(f, sep=';')
            if 'algorithm name' not in df.columns:
                continue
        except Exception:
            continue
        
        try:
            budget_idx = df.columns.tolist().index("parameters")
        except ValueError:
            budget_idx = 3 
            
        query_cols = [c for c in df.columns if str(c).endswith('.sql')]
            
        for _, row in df.iterrows():
            db = row['db system'].upper()
            run_date = row.get('date', '')
            memory_bytes = row.get('memory consumption', 0)
            
            budget = 0
            try:
                params_str = str(row.iloc[budget_idx]).replace("'", '"')
                params = json.loads(params_str)
                val = params.get('budget_MB', 0)
                budget = float(val[0]) if isinstance(val, list) else float(val)
            except:
                pass
            
            q_runtimes = {}
            q_costs = {}
            for q_col in query_cols:
                val = row[q_col]
                if pd.notna(val) and isinstance(val, str):
                    try:
                        q_data = json.loads(val)
                        runtimes = q_data.get('Runtimes', [])
                        cost = q_data.get('Cost')
                        if runtimes and len(runtimes) > 0 and runtimes[0] is not None:
                            q_runtimes[q_col] = float(runtimes[0])
                        if cost is not None:
                            q_costs[q_col] = float(cost)
                    except: pass
            
            idx_set = parse_indexes(row.get('indexed columns', ''))
            
            raw_data.append({
                'date': run_date,
                'db': db,
                'budget': budget,
                'utilized_mb': float(memory_bytes) / (1024.0 * 1024.0) if pd.notna(memory_bytes) else 0.0,
                'indexes': idx_set,
                'num_indexes': len(idx_set),
                'q_runtimes': q_runtimes,
                'q_costs': q_costs
            })
            
    return pd.DataFrame(raw_data)

# 1. Load Data
df = load_data()

# 2. Deduplicate: Keep only the freshest runs per database/budget
df = df.sort_values('date', ascending=False).groupby(['db', 'budget']).first().reset_index()

# 3. Identify Safe Core Queries
all_query_cols = set()
for r_dict in df['q_runtimes']:
    all_query_cols.update(r_dict.keys())

timed_out_queries = set()
for r_dict in df['q_runtimes']:
    for q in all_query_cols:
        if q not in r_dict:
            timed_out_queries.add(q)

safe_core = [q for q in all_query_cols if q not in timed_out_queries]

print(f"--- Data Filtering (Option 3: Safe Core) ---")
print(f"Total Unique Queries Discovered: {len(all_query_cols)}")
print(f"Queries Excluded (Timeout or Missing): {len(timed_out_queries)}")
print(f"Safe Core Queries Plotted: {len(safe_core)}\n")

# 4. Calculate final unskewed runtime and estimated cost using ONLY the safe core
df['runtime_s'] = df['q_runtimes'].apply(lambda r_dict: sum(r_dict.get(q, 0) for q in safe_core) / 1000.0)
df['cost_total'] = df['q_costs'].apply(lambda c_dict: sum(c_dict.get(q, 0) for q in safe_core))

# 5. Extract Baselines (USING BUDGET = 0, NOT ALGORITHM NAME)
baselines = df[df['budget'] == 0].set_index('db')['runtime_s'].to_dict()

# 6. Evaluation DataFrame
eval_df = df.copy().sort_values('budget')
eval_df['speedup'] = eval_df.apply(lambda r: baselines.get(r['db'], 0) / r['runtime_s'] if r['runtime_s'] > 0 else 0, axis=1)

# --- PLOT 1: Runtime vs Budget (Safe Core Only) ---
plt.figure(figsize=(10, 6))
sns.lineplot(data=eval_df, x='budget', y='runtime_s', hue='db', palette=DB_COLORS, marker='o', linewidth=2.5, markersize=8)
if 'POSTGRES' in baselines:
    plt.axhline(baselines['POSTGRES'], color=DB_COLORS['POSTGRES'], linestyle='--', alpha=0.5, label='PG Baseline')
if 'DB2' in baselines:
    plt.axhline(baselines['DB2'], color=DB_COLORS['DB2'], linestyle='--', alpha=0.5, label='DB2 Baseline')
plt.title(f'Workload Runtime vs Index Budget\n(Calculated exactly on the {len(safe_core)} Safe Core Queries)', fontsize=16)
plt.xlabel('Index Budget (MB)', fontsize=12)
plt.ylabel('Runtime (seconds)', fontsize=12)
plt.legend(title='Database', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.show()

# --- PLOT 2: Speedup vs Budget ---
plt.figure(figsize=(10, 6))
sns.lineplot(data=eval_df, x='budget', y='speedup', hue='db', palette=DB_COLORS, marker='s', linewidth=2.5, markersize=8)
plt.axhline(1.0, color='gray', linestyle='--', label='Baseline (1.0x)')
plt.title(f'Speedup relative to 0-Index Baseline\n(Calculated exactly on the {len(safe_core)} Safe Core Queries)', fontsize=16)
plt.xlabel('Index Budget (MB)', fontsize=12)
plt.ylabel('Speedup Factor (Higher is better)', fontsize=12)
plt.legend(title='Database', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.show()

# --- PLOT 3: Number of Indexes Used ---
idx_df = eval_df[eval_df['budget'] > 0]
plt.figure(figsize=(10, 6))
sns.barplot(data=idx_df, x='budget', y='num_indexes', hue='db', palette=DB_COLORS)
plt.title('Number of Indexes Recommended by db2advis', fontsize=16)
plt.xlabel('Index Budget (MB)', fontsize=12)
plt.ylabel('Total Indexes Selected', fontsize=12)
plt.legend(title='Database', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()
plt.show()

# --- PLOT 4: Index Overlap Analysis ---
budgets = sorted(idx_df['budget'].unique())
overlap_data = []

for b in budgets:
    pg_row = idx_df[(idx_df['db'] == 'POSTGRES') & (idx_df['budget'] == b)]
    db2_row = idx_df[(idx_df['db'] == 'DB2') & (idx_df['budget'] == b)]
    
    if not pg_row.empty and not db2_row.empty:
        pg_idx = pg_row.iloc[0]['indexes']
        db2_idx = db2_row.iloc[0]['indexes']
        
        common = len(pg_idx.intersection(db2_idx))
        pg_only = len(pg_idx - db2_idx)
        db2_only = len(db2_idx - pg_idx)
        
        overlap_data.append({
            'Budget': b,
            'Common': common,
            'Postgres Unique': pg_only,
            'DB2 Unique': db2_only
        })

if overlap_data:
    overlap_df = pd.DataFrame(overlap_data).set_index('Budget')
    overlap_df.plot(kind='bar', stacked=True, figsize=(10, 6), colormap='viridis')
    plt.title('Index Overlap Comparison (Common vs Unique)', fontsize=16)
    plt.xlabel('Index Budget (MB)', fontsize=12)
    plt.ylabel('Number of Indexes', fontsize=12)
    plt.legend(title='Index Type', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()

# --- PLOT 5: Optimizer Estimated Cost vs Actual Runtime ---
# Since Postgres and DB2 use completely different arbitrary units for 'Cost', we plot them on separate subplots
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

for i, db in enumerate(['POSTGRES', 'DB2']):
    # Sort by actual runtime to make the line graph flow left-to-right
    db_df = eval_df[eval_df['db'] == db].sort_values('runtime_s')
    if not db_df.empty:
        sns.lineplot(
            data=db_df, 
            x='runtime_s', 
            y='cost_total', 
            marker='o', 
            linewidth=2.5, 
            ax=axes[i],
            color=DB_COLORS.get(db, 'black')
        )
        axes[i].set_title(f'{db}: Estimated Cost vs Actual Runtime', fontsize=14)
        axes[i].set_xlabel('Total Actual Runtime (seconds)', fontsize=12)
        axes[i].set_ylabel('Total Estimated Cost (Arbitrary Units)', fontsize=12)

plt.tight_layout()
plt.show()
```
