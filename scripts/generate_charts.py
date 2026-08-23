import pandas as pd
import matplotlib.pyplot as plt
import json
import os
import re

DB2_FILE = "benchmark_results/results_db2advis_JOB_db2_114_queries.csv"
PG_FILE = "benchmark_results/results_db2advis_JOB_postgres_114_queries.csv"
OUTPUT_DIR = "benchmark_results/charts"

def parse_indexes(idx_str):
    # Format: [I(C movie_companies.company_type_id), I(C aka_name.person_id), ...]
    if pd.isna(idx_str):
        return set()
    
    # Simple regex to extract index columns
    matches = re.findall(r'I\((.*?)\)', idx_str)
    return set(matches)

def process_file(filepath):
    data = []
    if not os.path.exists(filepath):
        print(f"File {filepath} not found!")
        return pd.DataFrame()
        
    with open(filepath, 'r') as f:
        lines = f.readlines()
        if not lines:
            return pd.DataFrame()
            
        header = lines[0].strip().split(';')
        
        for line in lines[1:]:
            parts = line.strip().split(';')
            if len(parts) < len(header):
                continue
                
            row_dict = dict(zip(header[:len(parts)-1], parts[:-1]))
            row_dict['indexed columns'] = parts[-1]
            
            # Parse parameters
            try:
                params = json.loads(row_dict['parameters'])
                budget = params.get('budget_MB', 0)
                
                # If budget is a list, maybe take the first one? In the CSV it's usually just an int if it's one run per row.
                if isinstance(budget, list):
                    budget = budget[0]
                row_dict['budget_MB'] = budget
            except:
                row_dict['budget_MB'] = 0
                
            # Parse query runtimes
            total_runtime = 0
            query_runtimes = {}
            for k, v in row_dict.items():
                if k.endswith('.sql'):
                    try:
                        q_data = json.loads(v)
                        runtime = q_data.get('Runtimes', [0])[0]
                        total_runtime += runtime
                        query_runtimes[k] = runtime
                    except:
                        pass
            row_dict['total_runtime_s'] = total_runtime / 1000.0
            row_dict['query_runtimes'] = query_runtimes
            
            # Parse metrics
            row_dict['algorithm_runtime'] = float(row_dict.get('algorithm runtime', 0))
            row_dict['index_create_time'] = float(row_dict.get('index create time', 0))
            row_dict['num_indexes'] = int(row_dict.get('#indexes', 0))
            row_dict['index_set'] = parse_indexes(row_dict['indexed columns'])
            
            data.append(row_dict)
            
    return pd.DataFrame(data)

def generate_charts():
    df_db2 = process_file(DB2_FILE)
    df_pg = process_file(PG_FILE)
    
    if df_db2.empty or df_pg.empty:
        print("Empty dataframe(s), cannot generate charts.")
        return
        
    # Group by budget
    db2_budgets = df_db2.groupby('budget_MB').first().sort_index()
    pg_budgets = df_pg.groupby('budget_MB').first().sort_index()
    
    # Common budgets
    common_budgets = list(set(db2_budgets.index).intersection(set(pg_budgets.index)))
    common_budgets.sort()
    
    print(f"Generating charts for budgets: {common_budgets}")
    
    # 1. Total number of indexes created
    plt.figure(figsize=(10, 6))
    plt.plot(common_budgets, [db2_budgets.loc[b]['num_indexes'] for b in common_budgets], marker='o', label='DB2')
    plt.plot(common_budgets, [pg_budgets.loc[b]['num_indexes'] for b in common_budgets], marker='s', label='PostgreSQL')
    plt.title('Number of Selected Indexes by Budget')
    plt.xlabel('Budget (MB)')
    plt.ylabel('Number of Indexes')
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend()
    plt.savefig(f'{OUTPUT_DIR}/num_indexes.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 2. Index intersection
    intersections = []
    db2_counts = []
    pg_counts = []
    for b in common_budgets:
        db2_set = db2_budgets.loc[b]['index_set']
        pg_set = pg_budgets.loc[b]['index_set']
        intersect = len(db2_set.intersection(pg_set))
        intersections.append(intersect)
        db2_counts.append(len(db2_set))
        pg_counts.append(len(pg_set))
        
    plt.figure(figsize=(10, 6))
    x = range(len(common_budgets))
    width = 0.25
    plt.bar([i - width for i in x], db2_counts, width, label='DB2 Only (Total)', color='#1f77b4', alpha=0.6)
    plt.bar([i + width for i in x], pg_counts, width, label='PostgreSQL Only (Total)', color='#ff7f0e', alpha=0.6)
    plt.bar(x, intersections, width, label='Intersection (Common)', color='#2ca02c')
    plt.xticks(x, common_budgets)
    plt.title('Index Sets: DB2 vs PostgreSQL')
    plt.xlabel('Budget (MB)')
    plt.ylabel('Number of Indexes')
    plt.legend()
    plt.grid(True, axis='y', linestyle='--', alpha=0.7)
    plt.savefig(f'{OUTPUT_DIR}/index_intersection.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 3. Total workload runtime
    plt.figure(figsize=(10, 6))
    plt.plot(common_budgets, [db2_budgets.loc[b]['total_runtime_s'] for b in common_budgets], marker='o', label='DB2')
    plt.plot(common_budgets, [pg_budgets.loc[b]['total_runtime_s'] for b in common_budgets], marker='s', label='PostgreSQL')
    plt.title('Total Workload Runtime (114 Queries)')
    plt.xlabel('Budget (MB)')
    plt.ylabel('Runtime (seconds)')
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend()
    plt.savefig(f'{OUTPUT_DIR}/total_runtime.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 4. Algorithm Overhead
    plt.figure(figsize=(10, 6))
    plt.plot(common_budgets, [db2_budgets.loc[b]['algorithm_runtime'] for b in common_budgets], marker='o', label='DB2 Algorithm (Virtual)', color='#1f77b4')
    plt.plot(common_budgets, [pg_budgets.loc[b]['algorithm_runtime'] for b in common_budgets], marker='s', label='PostgreSQL Algorithm (HypoPG)', color='#ff7f0e')
    plt.plot(common_budgets, [db2_budgets.loc[b]['index_create_time'] for b in common_budgets], marker='^', linestyle='--', label='DB2 Physical Index Creation', color='#1f77b4')
    plt.plot(common_budgets, [pg_budgets.loc[b]['index_create_time'] for b in common_budgets], marker='v', linestyle='--', label='PostgreSQL Physical Index Creation', color='#ff7f0e')
    
    plt.title('Algorithm Overhead and Index Creation Time')
    plt.xlabel('Budget (MB)')
    plt.ylabel('Time (seconds)')
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend()
    plt.savefig(f'{OUTPUT_DIR}/algorithm_overhead.png', dpi=300, bbox_inches='tight')
    plt.close()

    # 5. Individual Query Runtimes (for 10000MB budget)
    target_budget = 10000
    if target_budget in common_budgets:
        db2_qs = db2_budgets.loc[target_budget]['query_runtimes']
        pg_qs = pg_budgets.loc[target_budget]['query_runtimes']
        
        queries = list(db2_qs.keys())
        # Sort queries by DB2 runtime descending to see biggest queries
        queries.sort(key=lambda q: db2_qs[q], reverse=True)
        top_queries = queries[:30] # Top 30
        
        plt.figure(figsize=(14, 8))
        x = range(len(top_queries))
        plt.bar([i - 0.2 for i in x], [db2_qs[q]/1000.0 for q in top_queries], 0.4, label='DB2')
        plt.bar([i + 0.2 for i in x], [pg_qs[q]/1000.0 for q in top_queries], 0.4, label='PostgreSQL')
        plt.xticks(x, [q.replace('.sql','') for q in top_queries], rotation=90)
        plt.title(f'Top 30 Queries Runtime (Budget: {target_budget} MB)')
        plt.xlabel('Query')
        plt.ylabel('Runtime (seconds)')
        plt.legend()
        plt.grid(True, axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()
        plt.savefig(f'{OUTPUT_DIR}/query_runtimes_10000MB.png', dpi=300)
        plt.close()
        
    print("Done generating charts.")

if __name__ == "__main__":
    generate_charts()
