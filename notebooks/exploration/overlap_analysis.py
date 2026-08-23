import pandas as pd
import re
import matplotlib.pyplot as plt
import seaborn as sns
import matplotlib.ticker as ticker
import os

budgets = [250, 500, 1000, 1750, 2500, 3000, 3500, 4000, 4500, 5000, 6250, 7500, 10000, 12500, 15000]
dbs = ['postgres', 'db2']

def get_indexes(csv_file, budget):
    try:
        with open(csv_file, 'r') as f:
            lines = f.readlines()
        
        for line in lines[1:]:
            if f'"budget_MB": {budget}' in line or f'"budget_MB": [{budget}' in line or f'"budget_MB": {float(budget)}' in line:
                # Indexes are at the very end of the line: [I(C ...), I(C ...)]
                match = re.search(r'(\[I\(C .*\])$', line.strip())
                if match:
                    idx_str = match.group(1)
                    # Extract individual index definitions
                    indexes = re.findall(r'I\((.*?)\)', idx_str)
                    
                    # Canonicalize (sort columns so A,B is same as B,A)
                    canonical_indexes = set()
                    for idx in indexes:
                        cols = tuple(sorted([c.strip() for c in idx.split(',')]))
                        canonical_indexes.add(cols)
                    return canonical_indexes
        return set()
    except Exception as e:
        print(f"Error reading {csv_file}: {e}")
        return set()

records = []
for db in dbs:
    for b in budgets:
        ext_idx = get_indexes(f'benchmark_results/results_extend_JOB_REFINED_{db}_33_queries.csv', b)
        adv_idx = get_indexes(f'benchmark_results/results_db2advis_JOB_REFINED_{db}_33_queries.csv', b)
        
        overlap = ext_idx.intersection(adv_idx)
        union = ext_idx.union(adv_idx)
        jaccard = len(overlap) / len(union) if union else 0
        
        records.append({
            'Database': db.upper(),
            'Budget': b,
            'Extend_Count': len(ext_idx),
            'Db2advis_Count': len(adv_idx),
            'Overlap_Count': len(overlap),
            'Jaccard_Similarity': jaccard * 100  # As percentage
        })

df = pd.DataFrame(records)

# Print Summary
print("==========================================================================")
print("INDEX OVERLAP ANALYSIS (Extend vs Db2advis)")
print("==========================================================================")
print(df.to_string(index=False))
print("==========================================================================")

# Generate Plot
sns.set_theme(style='whitegrid', font_scale=1.05)
PALETTE = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8'}

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
fig.suptitle('Index Overlap Analysis: Extend vs db2advis', fontsize=14, fontweight='bold', y=1.04)

# Plot 1: Absolute Overlap Count
for db in ['POSTGRES', 'DB2']:
    sub = df[df['Database'] == db]
    axes[0].plot(sub['Budget'], sub['Overlap_Count'], marker='o', linewidth=2.3, label=db, color=PALETTE[db])
axes[0].set_title('Absolute Number of Overlapping Indexes', fontsize=12)
axes[0].set_xlabel('Index Budget (MB)')
axes[0].set_ylabel('Number of Identical Indexes Picked')
axes[0].legend(title='Database')
axes[0].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

# Plot 2: Jaccard Similarity (%)
for db in ['POSTGRES', 'DB2']:
    sub = df[df['Database'] == db]
    axes[1].plot(sub['Budget'], sub['Jaccard_Similarity'], marker='o', linewidth=2.3, label=db, color=PALETTE[db])
axes[1].set_title('Jaccard Similarity (Overlap Percentage)', fontsize=12)
axes[1].set_xlabel('Index Budget (MB)')
axes[1].set_ylabel('Similarity (%)')
axes[1].legend(title='Database')
axes[1].set_ylim(0, 100)
axes[1].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

plt.tight_layout()
os.makedirs('benchmark_results/charts', exist_ok=True)
plt.savefig('benchmark_results/charts/proof_7_index_overlap.png', bbox_inches='tight')
print("\nSaved chart to benchmark_results/charts/proof_7_index_overlap.png")
