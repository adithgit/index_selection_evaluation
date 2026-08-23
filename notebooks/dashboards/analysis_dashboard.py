import pandas as pd
import json
import numpy as np
from scipy.stats import gmean
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import seaborn as sns
import warnings
import sys
import os
warnings.filterwarnings('ignore')

# ────────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────────

def parse_dict(s):
    s = str(s).replace("'", '"').replace('None', 'null')
    return json.loads(s)

def get_baseline_runtimes(csv_file):
    try:
        df = pd.read_csv(csv_file, sep=';')
        row = df.iloc[-1]
        runtimes = {}
        for col in df.columns:
            if col.endswith('.sql'):
                try:
                    val = parse_dict(row[col])
                    if val.get('Runtimes') and val['Runtimes'][0] is not None:
                        runtimes[col] = val['Runtimes'][0]
                except:
                    pass
        return runtimes
    except Exception as e:
        print(f"Failed to read {csv_file}: {e}")
        return {}

def get_indexed_runtimes(csv_file, budget):
    try:
        df = pd.read_csv(csv_file, sep=';')
        row = None
        for _, r in df.iterrows():
            if pd.isna(r['parameters']):
                continue
            try:
                p = json.loads(str(r['parameters']).replace("'", '"'))
                b = p.get('budget_MB')
                if b == budget or (isinstance(b, list) and budget in b):
                    row = r  # keep updating to use the most recent match
            except Exception:
                pass
        if row is None:
            return {}
        runtimes = {}
        for col in df.columns:
            if col.endswith('.sql'):
                try:
                    val = parse_dict(row[col])
                    if val.get('Runtimes') and val['Runtimes'][0] is not None:
                        runtimes[col] = val['Runtimes'][0]
                except:
                    pass
        return runtimes
    except Exception as e:
        print(f"Failed to read {csv_file}: {e}")
        return {}

# ────────────────────────────────────────────────────
# CONFIGURATION
# ────────────────────────────────────────────────────

db_names   = ['postgres', 'db2']
algorithms = ['extend', 'db2advis']
budgets    = [250, 500, 1000, 1750, 2500, 3000, 3500, 4000, 4500,
              5000, 6250, 7500, 10000, 12500, 15000]

# Query complexity classification (joins = tables in FROM clause - 1)
query_complexity = {
    '1b.sql': 'Simple',  '2c.sql': 'Simple',  '3c.sql': 'Simple', 
    '4b.sql': 'Simple',  '5a.sql': 'Simple',  '6e.sql': 'Simple', 
    '8b.sql': 'Simple',  '10a.sql': 'Simple', '17e.sql': 'Simple',
    '18b.sql': 'Simple', '32a.sql': 'Simple',
    '7b.sql': 'Intermediate',  '9c.sql': 'Intermediate',
    '11d.sql': 'Intermediate', '12a.sql': 'Intermediate',
    '13c.sql': 'Intermediate', '14c.sql': 'Intermediate',
    '15d.sql': 'Intermediate', '16a.sql': 'Intermediate',
    '19a.sql': 'Intermediate', '20c.sql': 'Intermediate',
    '21c.sql': 'Intermediate', '22d.sql': 'Intermediate',
    '23a.sql': 'Intermediate', '24a.sql': 'Intermediate',
    '25b.sql': 'Intermediate', '26b.sql': 'Intermediate',
    '27c.sql': 'Intermediate', '30c.sql': 'Intermediate',
    '31c.sql': 'Intermediate',
    '28b.sql': 'Complex', '29b.sql': 'Complex', '33a.sql': 'Complex',
}

# ────────────────────────────────────────────────────
# LOAD DATA
# ────────────────────────────────────────────────────

baselines = {}
for db in db_names:
    baselines[db] = get_baseline_runtimes(
        f'benchmark_results/results_no_index_JOB_REFINED_{db}_33_queries.csv')

# Collect records: one row per (budget, algo, db, query)
records = []

# Budget = 0 → baseline (no-index) point
for db in db_names:
    for q, rt in baselines[db].items():
        complexity = query_complexity.get(q, 'Complex')
        for alg in algorithms:
            records.append({
                'Budget': 0, 'Algorithm': alg.capitalize(),
                'Database': db.upper(), 'Query': q,
                'Complexity': complexity,
                'Runtime': rt, 'Speedup': 1.0,
            })

# Budget > 0 → indexed runs
for b in budgets:
    for db in db_names:
        for alg in algorithms:
            indexed = get_indexed_runtimes(
                f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            for q, base_rt in baselines[db].items():
                idx_rt = indexed.get(q) if indexed else None
                if not idx_rt or idx_rt <= 0:
                    idx_rt = base_rt          # impute dropout with baseline
                complexity = query_complexity.get(q, 'Complex')
                records.append({
                    'Budget': b, 'Algorithm': alg.capitalize(),
                    'Database': db.upper(), 'Query': q,
                    'Complexity': complexity,
                    'Runtime': idx_rt,
                    'Speedup': base_rt / idx_rt,
                })

df = pd.DataFrame(records)
df['Complexity'] = pd.Categorical(
    df['Complexity'], categories=['Simple', 'Intermediate', 'Complex'], ordered=True)

# ────────────────────────────────────────────────────
# DERIVED AGGREGATES
# ────────────────────────────────────────────────────

# Per (Budget, Algorithm, Database): arithmetic mean runtime, geometric mean runtime
agg_runtime = (
    df.groupby(['Budget', 'Algorithm', 'Database'])['Runtime']
    .agg(Arithmetic=np.mean, Geometric=gmean)
    .reset_index()
)

# Per (Budget, Algorithm, Database, Complexity): geometric mean runtime broken down by query category
agg_runtime_cat = (
    df.groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Runtime']
    .agg(Geometric=gmean)
    .reset_index()
)

# Per (Budget, Algorithm, Database): geometric mean speedup ratio (GMSR)
agg_gmsr = (
    df[df['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
    .apply(gmean)
    .reset_index(name='GMSR')
)

def trimmed_mean(x):
    n = len(x)
    if n > 6:
        return np.mean(np.sort(x)[3:-3])
    return np.mean(x)

# Per (Budget, Algorithm, Database): Trimmed Speedups
agg_robust_speedup = (
    df[df['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
    .agg(Trimmed=trimmed_mean)
    .reset_index()
)

# Per (Budget, Algorithm, Database, Complexity): GMSR broken down by query category
agg_gmsr_cat = (
    df[df['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Speedup']
    .apply(gmean)
    .reset_index(name='GMSR')
)

# ────────────────────────────────────────────────────
# STYLING
# ────────────────────────────────────────────────────

sns.set_theme(style='whitegrid', font_scale=1.05)
PALETTE   = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8'}
ALG_ORDER = ['Extend', 'Db2advis']
ALG_LABEL = {'Extend': 'Extend', 'Db2advis': 'db2advis'}

COMPLEXITY_COLORS = {
    'Simple':       '#2196F3',
    'Intermediate': '#FF9800',
    'Complex':      '#9C27B0',
}
COMPLEXITY_ORDER = ['Simple', 'Intermediate', 'Complex']

# Helper: render a LaTeX-style equation as figure subtitle using mathtext
def add_equation(fig, equation, y=0.01, fontsize=11):
    fig.text(0.5, y, equation, ha='center', va='bottom',
             fontsize=fontsize, color='#444444',
             fontfamily='monospace',
             bbox=dict(boxstyle='round,pad=0.4', fc='#F5F5F5',
                       ec='#CCCCCC', alpha=0.9))


# ═════════════════════════════════════════════════
# PLOT 1 — ARITHMETIC MEAN RUNTIME
# ═════════════════════════════════════════════════

# Redirect stderr to suppress any warnings during plotting
old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot 1  —  Arithmetic Mean Query Runtime vs. Index Budget',
        fontsize=14, fontweight='bold', y=1.04
    )

    for ax, alg in zip(axes, ALG_ORDER):
        sub = agg_runtime[agg_runtime['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Arithmetic'],
                    marker='o', linewidth=2.3, label=db,
                    color=PALETTE[db])
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Arithmetic Mean Runtime (ms)')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    add_equation(
        fig,
        r'AM(budget) = (1/n) · Σ t_i(budget)     '
        r'where t_i = runtime of query i,  n = 33 queries',
        y=-0.04
    )
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_1_arithmetic_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 2 — GEOMETRIC MEAN RUNTIME
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot 2  —  Geometric Mean Query Runtime vs. Index Budget',
        fontsize=14, fontweight='bold', y=1.04
    )

    for ax, alg in zip(axes, ALG_ORDER):
        sub = agg_runtime[agg_runtime['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Geometric'],
                    marker='o', linewidth=2.3, label=db,
                    color=PALETTE[db])
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Geometric Mean Runtime (ms)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(
        ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    add_equation(
        fig,
        r'GM(budget) = exp( (1/n) · Σ ln(t_i(budget)) )'
        r'     appropriate for log-distributed runtime data',
        y=-0.04
    )
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_2_geometric_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 2b — GEOMETRIC MEAN RUNTIME BY QUERY COMPLEXITY CATEGORY
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
        fig.suptitle(
            f'Plot 2b  —  Geometric Mean Runtime by Query Complexity  ·  {ALG_LABEL[alg]}\n'
            'Simple (<=\u200B6 joins)  ·  Intermediate (7-11 joins)  ·  Complex (>=\u200B12 joins)',
            fontsize=13, fontweight='bold', y=1.04
        )

        sub_alg = agg_runtime_cat[agg_runtime_cat['Algorithm'] == alg]

        for ax, complexity in zip(axes, COMPLEXITY_ORDER):
            sub = sub_alg[sub_alg['Complexity'] == complexity]
            col = COMPLEXITY_COLORS[complexity]

            for db, grp in sub.groupby('Database'):
                ls = '-' if db == 'POSTGRES' else '--'
                ax.plot(grp['Budget'], grp['Geometric'],
                        marker='o', linewidth=2.2,
                        linestyle=ls, color=col,
                        label=db,
                        alpha=0.9 if db == 'POSTGRES' else 0.65)

            ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
            ax.set_xlabel('Index Budget (MB)')
            ax.set_ylabel('Geometric Mean Runtime (ms)')
            ax.legend(title='Database', fontsize=9)
            ax.xaxis.set_major_formatter(
                ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

            n_q = df[df['Complexity'] == complexity]['Query'].nunique()
            ax.text(0.97, 0.04, f'n = {n_q} queries',
                    transform=ax.transAxes, ha='right', va='bottom',
                    fontsize=8.5, color='grey')

        add_equation(
            fig,
            r'GM_c(budget) = exp( (1/n_c) * sum_{i in c} ln( t_i(budget) ) )'
            r'     c in {Simple, Intermediate, Complex}',
            y=-0.04
        )
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.14)
        plt.savefig(f'benchmark_results/charts/proof_2b_geometric_mean_complexity_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 3a — TRIMMED MEAN SPEEDUP
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot 3a  —  Trimmed Mean Speedup (10%) vs. Index Budget\n'
        'Removes top 10% and bottom 10% extreme speedups to find a robust trend',
        fontsize=13, fontweight='bold', y=1.04
    )

    LINESTYLE = {'POSTGRES': '-', 'DB2': '--'}

    for ax, alg in zip(axes, ALG_ORDER):
        sub = agg_robust_speedup[agg_robust_speedup['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Trimmed'],
                    marker='o', linewidth=2.3,
                    linestyle=LINESTYLE[db],
                    label=db, color=PALETTE[db])

        ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':',
                   alpha=0.7, label='No gain (1×)')
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Trimmed Mean Speedup')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    add_equation(
        fig,
        r'TrimmedMean(budget) = mean( speedups excluding top/bottom 10% )',
        y=-0.04
    )
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_3a_trimmed_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 4 — GEOMETRIC MEAN SPEEDUP RATIO (GMSR)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot 4  —  Geometric Mean Speedup Ratio (GMSR) vs. Index Budget\n'
        'Primary DB-agnostic metric  ·  GMSR > 1 means indexes help',
        fontsize=13, fontweight='bold', y=1.04
    )

    LINESTYLE = {'POSTGRES': '-', 'DB2': '--'}

    for ax, alg in zip(axes, ALG_ORDER):
        sub = agg_gmsr[agg_gmsr['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['GMSR'],
                    marker='o', linewidth=2.3,
                    linestyle=LINESTYLE[db],
                    label=db, color=PALETTE[db])

        ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':',
                   alpha=0.7, label='No gain (1×)')
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('GMSR  (higher = better)')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

        if alg == 'Extend':
            ax.annotate(
                'DB2: GMSR ≈ 1×\n(engine does not\nexploit indexes)',
                xy=(6000, 1.06), fontsize=8.5, color='#1B6CA8',
                bbox=dict(boxstyle='round,pad=0.35', fc='#EBF3FB',
                          ec='#1B6CA8', alpha=0.92)
            )

    add_equation(
        fig,
        r'GMSR(budget) = exp( (1/n) · Σ ln( t_i(no-index) / t_i(indexed) ) )'
        r'     n = 33 queries',
        y=-0.04
    )
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_4_gmsr.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 4b — GMSR BY QUERY COMPLEXITY CATEGORY
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
        fig.suptitle(
            f'Plot 4b  —  GMSR by Query Complexity  ·  {ALG_LABEL[alg]}\n'
            'Simple (<=\u200B6 joins)  ·  Intermediate (7-11 joins)  ·  Complex (>=\u200B12 joins)',
            fontsize=13, fontweight='bold', y=1.04
        )

        sub_alg = agg_gmsr_cat[agg_gmsr_cat['Algorithm'] == alg]

        for ax, complexity in zip(axes, COMPLEXITY_ORDER):
            sub = sub_alg[sub_alg['Complexity'] == complexity]
            col = COMPLEXITY_COLORS[complexity]

            for db, grp in sub.groupby('Database'):
                ls = '-' if db == 'POSTGRES' else '--'
                ax.plot(grp['Budget'], grp['GMSR'],
                        marker='o', linewidth=2.2,
                        linestyle=ls, color=col,
                        label=db,
                        alpha=0.9 if db == 'POSTGRES' else 0.65)

            ax.axhline(1.0, color='grey', linewidth=1, linestyle=':',
                       alpha=0.6, label='No gain (1x)')
            ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
            ax.set_xlabel('Index Budget (MB)')
            ax.set_ylabel('GMSR  (higher = better)')
            ax.legend(title='Database', fontsize=9)
            ax.xaxis.set_major_formatter(
                ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

            n_q = df[df['Complexity'] == complexity]['Query'].nunique()
            ax.text(0.97, 0.04, f'n = {n_q} queries',
                    transform=ax.transAxes, ha='right', va='bottom',
                    fontsize=8.5, color='grey')

        add_equation(
            fig,
            r'GMSR_c(budget) = exp( (1/n_c) * sum_{i in c} ln( t_i(no-index) / t_i(indexed) ) )'
            r'     c in {Simple, Intermediate, Complex}',
            y=-0.04
        )
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.14)
        plt.savefig(f'benchmark_results/charts/proof_4b_gmsr_complexity_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 5 — SPEEDUP IQR WIDTH vs. INDEX BUDGET
# ═════════════════════════════════════════════════

iqr_rows = []
for b in budgets:
    for alg in ALG_ORDER:
        for db in ['POSTGRES', 'DB2']:
            sub = df[(df['Budget'] == b) & (df['Algorithm'] == alg)
                     & (df['Database'] == db)]['Speedup'].dropna().values
            sub = sub[sub > 0]
            if len(sub) < 4:
                continue
            log_s = np.log10(sub)
            iqr_rows.append({
                'Budget': b, 'Algorithm': alg, 'Database': db,
                'Complexity': 'All',
                'IQR_log': float(np.percentile(log_s, 75) - np.percentile(log_s, 25)),
                'Q25': float(np.percentile(log_s, 25)),
                'Q75': float(np.percentile(log_s, 75)),
                'Median_log': float(np.median(log_s)),
            })
            for complexity in COMPLEXITY_ORDER:
                sub_c = df[(df['Budget'] == b) & (df['Algorithm'] == alg)
                           & (df['Database'] == db)
                           & (df['Complexity'] == complexity)]['Speedup'].dropna().values
                sub_c = sub_c[sub_c > 0]
                if len(sub_c) < 2:
                    continue
                log_c = np.log10(sub_c)
                iqr_rows.append({
                    'Budget': b, 'Algorithm': alg, 'Database': db,
                    'Complexity': complexity,
                    'IQR_log': float(np.percentile(log_c, 75) - np.percentile(log_c, 25)),
                    'Q25': float(np.percentile(log_c, 25)),
                    'Q75': float(np.percentile(log_c, 75)),
                    'Median_log': float(np.median(log_c)),
                })

df_iqr = pd.DataFrame(iqr_rows)

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot 5  —  Speedup IQR Width vs. Index Budget  (Variance Stability)\n'
        'Shrinking IQR = algorithm becomes more consistent  ·  '
        'Flat/growing IQR = persistent winners and losers',
        fontsize=13, fontweight='bold', y=1.04
    )

    DB_DASH = {'POSTGRES': '-', 'DB2': '--'}

    for ax, alg in zip(axes, ALG_ORDER):
        for complexity in COMPLEXITY_ORDER:
            col = COMPLEXITY_COLORS[complexity]
            for db in ['POSTGRES', 'DB2']:
                sub = df_iqr[(df_iqr['Algorithm'] == alg)
                             & (df_iqr['Database'] == db)
                             & (df_iqr['Complexity'] == complexity)]
                if sub.empty:
                    continue
                ax.plot(sub['Budget'], sub['IQR_log'],
                        linewidth=1.1, linestyle=DB_DASH[db],
                        color=col, alpha=0.30)

        for db in ['POSTGRES', 'DB2']:
            sub_all = df_iqr[(df_iqr['Algorithm'] == alg)
                             & (df_iqr['Database'] == db)
                             & (df_iqr['Complexity'] == 'All')]
            if sub_all.empty:
                continue
            ax.plot(sub_all['Budget'], sub_all['IQR_log'],
                    marker='o', linewidth=2.5,
                    linestyle=DB_DASH[db],
                    color=PALETTE[db], label=f'{db} (all queries)',
                    zorder=5)

        for complexity in COMPLEXITY_ORDER:
            ax.plot([], [], color=COMPLEXITY_COLORS[complexity],
                    linewidth=2, alpha=0.55, label=complexity)

        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('IQR of log₁₀(Speedup)  [log units]')
        ax.legend(fontsize=8.5, ncol=2, loc='upper left')
        ax.xaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

        ax.text(0.98, 0.97,
                'Higher = more variance\nLower = more consistent',
                transform=ax.transAxes, ha='right', va='top',
                fontsize=8, color='grey',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc', alpha=0.8))

    add_equation(
        fig,
        r'IQR_log(budget) = Q75( log10(Speedup_i(budget)) ) - Q25( log10(Speedup_i(budget)) )'
        r'     computed across all queries in the group',
        y=-0.04
    )
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_5_variance_stability.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT 6 — SPEEDUP DISTRIBUTION AT SELECTED BUDGETS
# ═════════════════════════════════════════════════

SELECTED_BUDGETS = [250, 3000, 7500, 15000]
BUDGET_LABELS    = {250: '250 MB', 3000: '3,000 MB',
                    7500: '7,500 MB', 15000: '15,000 MB'}

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(
            4, 3, figsize=(14, 18), sharey=False,
            gridspec_kw={'hspace': 0.45, 'wspace': 0.32}
        )
        fig.suptitle(
            f'Plot 6  —  Speedup Distribution at Selected Budgets  ·  {ALG_LABEL[alg]}\n'
            'Each row = one budget level  ·  columns = complexity tier  ·  log scale',
            fontsize=13, fontweight='bold', y=1.01
        )

        for row_i, budget in enumerate(SELECTED_BUDGETS):
            df_b = df[(df['Budget'] == budget) & (df['Algorithm'] == alg)].copy()

            for col_i, complexity in enumerate(COMPLEXITY_ORDER):
                ax = axes[row_i][col_i]
                df_bc = df_b[df_b['Complexity'] == complexity]

                sns.boxplot(
                    data=df_bc,
                    x='Database', y='Speedup',
                    order=['POSTGRES', 'DB2'],
                    palette=PALETTE,
                    width=0.5, linewidth=1.2,
                    flierprops=dict(marker='o', markersize=3,
                                    alpha=0.45, linestyle='none'),
                    ax=ax
                )

                ax.set_yscale('log')
                ax.axhline(1.0, color='grey', linewidth=1,
                           linestyle='--', alpha=0.65)
                ax.set_xlabel('')
                ax.set_ylabel('Speedup  (log)' if col_i == 0 else '')

                if row_i == 0:
                    n_q = df_bc['Query'].nunique()
                    ax.set_title(f'{complexity}\nn={n_q} queries',
                                 fontsize=10, fontweight='bold',
                                 color=COMPLEXITY_COLORS[complexity])
                else:
                    ax.set_title('')

                if col_i == 0:
                    ax.set_ylabel(
                        f'{BUDGET_LABELS[budget]}\n\nSpeedup  (log)',
                        fontsize=9
                    )

        add_equation(
            fig,
            r'Speedup_i(b) = t_i(no-index) / t_i(indexed, b)'
            r'     >1 = faster,  =1 = no change,  <1 = regression',
            y=-0.01
        )
        plt.savefig(f'benchmark_results/charts/proof_6_speedup_dist_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║         ALL-THREE-DB SECTION  (Postgres + DB2 + MySQL)                 ║
# ╚═════════════════════════════════════════════════════════════════════════╝

M_DB_NAMES   = ['postgres', 'db2', 'mysql']
M_PALETTE    = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8', 'MYSQL': '#2E7D32'}
M_LINESTYLE  = {'POSTGRES': '-',       'DB2': '--',       'MYSQL': ':'}
M_ALPHA      = {'POSTGRES': 0.9,       'DB2': 0.65,       'MYSQL': 0.8}
M_MARKER     = {'POSTGRES': 'o',       'DB2': 's',        'MYSQL': '^'}

m_baselines = {}
for db in M_DB_NAMES:
    m_baselines[db] = get_baseline_runtimes(
        f'benchmark_results/results_no_index_JOB_REFINED_{db}_33_queries.csv')

m_records = []

for db in M_DB_NAMES:
    for q, rt in m_baselines[db].items():
        complexity = query_complexity.get(q, 'Complex')
        for alg in algorithms:
            m_records.append({
                'Budget': 0, 'Algorithm': alg.capitalize(),
                'Database': db.upper(), 'Query': q,
                'Complexity': complexity,
                'Runtime': rt, 'Speedup': 1.0,
            })

for b in budgets:
    for db in M_DB_NAMES:
        for alg in algorithms:
            indexed = get_indexed_runtimes(
                f'benchmark_results/results_{alg}_JOB_REFINED_{db}_33_queries.csv', b)
            for q, base_rt in m_baselines[db].items():
                idx_rt = indexed.get(q) if indexed else None
                if not idx_rt or idx_rt <= 0:
                    idx_rt = base_rt
                complexity = query_complexity.get(q, 'Complex')
                m_records.append({
                    'Budget': b, 'Algorithm': alg.capitalize(),
                    'Database': db.upper(), 'Query': q,
                    'Complexity': complexity,
                    'Runtime': idx_rt,
                    'Speedup': base_rt / idx_rt,
                })

mdf = pd.DataFrame(m_records)
mdf['Complexity'] = pd.Categorical(
    mdf['Complexity'], categories=['Simple', 'Intermediate', 'Complex'], ordered=True)

m_agg_runtime = (
    mdf.groupby(['Budget', 'Algorithm', 'Database'])['Runtime']
    .agg(Arithmetic=np.mean, Geometric=gmean)
    .reset_index()
)

m_agg_runtime_cat = (
    mdf.groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Runtime']
    .agg(Geometric=gmean)
    .reset_index()
)

m_agg_gmsr = (
    mdf[mdf['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
    .apply(gmean)
    .reset_index(name='GMSR')
)

m_agg_robust_speedup = (
    mdf[mdf['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
    .agg(Trimmed=trimmed_mean)
    .reset_index()
)

m_agg_gmsr_cat = (
    mdf[mdf['Budget'] > 0]
    .groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Speedup']
    .apply(gmean)
    .reset_index(name='GMSR')
)


# ═════════════════════════════════════════════════
# PLOT M1 — ARITHMETIC MEAN RUNTIME  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot M1  —  Arithmetic Mean Query Runtime vs. Index Budget  (Postgres + DB2 + MySQL)',
        fontsize=14, fontweight='bold', y=1.04
    )
    for ax, alg in zip(axes, ALG_ORDER):
        sub = m_agg_runtime[m_agg_runtime['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Arithmetic'],
                    marker=M_MARKER[db], linewidth=2.3,
                    linestyle=M_LINESTYLE[db], color=M_PALETTE[db],
                    label=db)
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Arithmetic Mean Runtime (ms)')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    add_equation(fig,
        r'AM(budget) = (1/n) · Σ t_i(budget)     where t_i = runtime of query i,  n = 33 queries',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_M1_arithmetic_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M2 — GEOMETRIC MEAN RUNTIME  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot M2  —  Geometric Mean Query Runtime vs. Index Budget  (Postgres + DB2 + MySQL)',
        fontsize=14, fontweight='bold', y=1.04
    )
    for ax, alg in zip(axes, ALG_ORDER):
        sub = m_agg_runtime[m_agg_runtime['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Geometric'],
                    marker=M_MARKER[db], linewidth=2.3,
                    linestyle=M_LINESTYLE[db], color=M_PALETTE[db],
                    label=db)
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Geometric Mean Runtime (ms)')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    add_equation(fig,
        r'GM(budget) = exp( (1/n) · Σ ln(t_i(budget)) )     appropriate for log-distributed runtime data',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_M2_geometric_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M2b — GEOMETRIC MEAN RUNTIME BY COMPLEXITY  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
        fig.suptitle(
            f'Plot M2b  —  Geometric Mean Runtime by Complexity  ·  {ALG_LABEL[alg]}  (all 3 DBs)\n'
            'Simple (≤6 joins)  ·  Intermediate (7-11 joins)  ·  Complex (≥12 joins)',
            fontsize=13, fontweight='bold', y=1.04
        )
        sub_alg = m_agg_runtime_cat[m_agg_runtime_cat['Algorithm'] == alg]
        for ax, complexity in zip(axes, COMPLEXITY_ORDER):
            sub = sub_alg[sub_alg['Complexity'] == complexity]
            col = COMPLEXITY_COLORS[complexity]
            for db, grp in sub.groupby('Database'):
                ax.plot(grp['Budget'], grp['Geometric'],
                        marker=M_MARKER[db], linewidth=2.2,
                        linestyle=M_LINESTYLE[db], color=col,
                        label=db, alpha=M_ALPHA[db])
            ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
            ax.set_xlabel('Index Budget (MB)')
            ax.set_ylabel('Geometric Mean Runtime (ms)')
            ax.legend(title='Database', fontsize=9)
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
            n_q = mdf[mdf['Complexity'] == complexity]['Query'].nunique()
            ax.text(0.97, 0.04, f'n = {n_q} queries',
                    transform=ax.transAxes, ha='right', va='bottom', fontsize=8.5, color='grey')
        add_equation(fig,
            r'GM_c(budget) = exp( (1/n_c) * sum_{i in c} ln( t_i(budget) ) )     c in {Simple, Intermediate, Complex}',
            y=-0.04)
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.14)
        plt.savefig(f'benchmark_results/charts/proof_M2b_geometric_mean_complexity_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M3a — TRIMMED MEAN SPEEDUP  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot M3a  —  Trimmed Mean Speedup (10%) vs. Index Budget  (Postgres + DB2 + MySQL)\n'
        'Removes top/bottom 10% extreme speedups',
        fontsize=13, fontweight='bold', y=1.04
    )
    for ax, alg in zip(axes, ALG_ORDER):
        sub = m_agg_robust_speedup[m_agg_robust_speedup['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Trimmed'],
                    marker=M_MARKER[db], linewidth=2.3,
                    linestyle=M_LINESTYLE[db], label=db, color=M_PALETTE[db])
        ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Trimmed Mean Speedup')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    add_equation(fig,
        r'TrimmedMean(budget) = mean( speedups excluding top/bottom 10% )',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_M3a_trimmed_mean.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M4 — GMSR  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot M4  —  Geometric Mean Speedup Ratio (GMSR) vs. Index Budget  (Postgres + DB2 + MySQL)\n'
        'Primary DB-agnostic metric  ·  GMSR > 1 means indexes help',
        fontsize=13, fontweight='bold', y=1.04
    )
    for ax, alg in zip(axes, ALG_ORDER):
        sub = m_agg_gmsr[m_agg_gmsr['Algorithm'] == alg]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['GMSR'],
                    marker=M_MARKER[db], linewidth=2.3,
                    linestyle=M_LINESTYLE[db], label=db, color=M_PALETTE[db])
        ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('GMSR  (higher = better)')
        ax.legend(title='Database')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    add_equation(fig,
        r'GMSR(budget) = exp( (1/n) · Σ ln( t_i(no-index) / t_i(indexed) ) )     n = 33 queries',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_M4_gmsr.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M4b — GMSR BY COMPLEXITY  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
        fig.suptitle(
            f'Plot M4b  —  GMSR by Query Complexity  ·  {ALG_LABEL[alg]}  (all 3 DBs)\n'
            'Simple (≤6 joins)  ·  Intermediate (7-11 joins)  ·  Complex (≥12 joins)',
            fontsize=13, fontweight='bold', y=1.04
        )
        sub_alg = m_agg_gmsr_cat[m_agg_gmsr_cat['Algorithm'] == alg]
        for ax, complexity in zip(axes, COMPLEXITY_ORDER):
            sub = sub_alg[sub_alg['Complexity'] == complexity]
            col = COMPLEXITY_COLORS[complexity]
            for db, grp in sub.groupby('Database'):
                ax.plot(grp['Budget'], grp['GMSR'],
                        marker=M_MARKER[db], linewidth=2.2,
                        linestyle=M_LINESTYLE[db], color=col,
                        label=db, alpha=M_ALPHA[db])
            ax.axhline(1.0, color='grey', linewidth=1, linestyle=':', alpha=0.6, label='No gain (1x)')
            ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
            ax.set_xlabel('Index Budget (MB)')
            ax.set_ylabel('GMSR  (higher = better)')
            ax.legend(title='Database', fontsize=9)
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
            n_q = mdf[mdf['Complexity'] == complexity]['Query'].nunique()
            ax.text(0.97, 0.04, f'n = {n_q} queries',
                    transform=ax.transAxes, ha='right', va='bottom', fontsize=8.5, color='grey')
        add_equation(fig,
            r'GMSR_c(budget) = exp( (1/n_c) * sum_{i in c} ln( t_i(no-index) / t_i(indexed) ) )     c in {Simple, Intermediate, Complex}',
            y=-0.04)
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.14)
        plt.savefig(f'benchmark_results/charts/proof_M4b_gmsr_complexity_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M5 — SPEEDUP IQR WIDTH  (all 3 DBs)
# ═════════════════════════════════════════════════

m_iqr_rows = []
for b in budgets:
    for alg in ALG_ORDER:
        for db in [d.upper() for d in M_DB_NAMES]:
            sub = mdf[(mdf['Budget'] == b) & (mdf['Algorithm'] == alg)
                      & (mdf['Database'] == db)]['Speedup'].dropna().values
            sub = sub[sub > 0]
            if len(sub) < 4:
                continue
            log_s = np.log10(sub)
            m_iqr_rows.append({
                'Budget': b, 'Algorithm': alg, 'Database': db,
                'Complexity': 'All',
                'IQR_log': float(np.percentile(log_s, 75) - np.percentile(log_s, 25)),
                'Q25': float(np.percentile(log_s, 25)),
                'Q75': float(np.percentile(log_s, 75)),
                'Median_log': float(np.median(log_s)),
            })
            for complexity in COMPLEXITY_ORDER:
                sub_c = mdf[(mdf['Budget'] == b) & (mdf['Algorithm'] == alg)
                            & (mdf['Database'] == db)
                            & (mdf['Complexity'] == complexity)]['Speedup'].dropna().values
                sub_c = sub_c[sub_c > 0]
                if len(sub_c) < 2:
                    continue
                log_c = np.log10(sub_c)
                m_iqr_rows.append({
                    'Budget': b, 'Algorithm': alg, 'Database': db,
                    'Complexity': complexity,
                    'IQR_log': float(np.percentile(log_c, 75) - np.percentile(log_c, 25)),
                    'Q25': float(np.percentile(log_c, 25)),
                    'Q75': float(np.percentile(log_c, 75)),
                    'Median_log': float(np.median(log_c)),
                })

mdf_iqr = pd.DataFrame(m_iqr_rows)

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(
        'Plot M5  —  Speedup IQR Width vs. Index Budget  (Postgres + DB2 + MySQL)\n'
        'Shrinking IQR = more consistent  ·  Flat/growing = persistent winners and losers',
        fontsize=13, fontweight='bold', y=1.04
    )
    for ax, alg in zip(axes, ALG_ORDER):
        for complexity in COMPLEXITY_ORDER:
            col = COMPLEXITY_COLORS[complexity]
            for db in [d.upper() for d in M_DB_NAMES]:
                sub = mdf_iqr[(mdf_iqr['Algorithm'] == alg)
                              & (mdf_iqr['Database'] == db)
                              & (mdf_iqr['Complexity'] == complexity)]
                if sub.empty:
                    continue
                ax.plot(sub['Budget'], sub['IQR_log'],
                        linewidth=1.1, linestyle=M_LINESTYLE[db], color=col, alpha=0.30)
        for db in [d.upper() for d in M_DB_NAMES]:
            sub_all = mdf_iqr[(mdf_iqr['Algorithm'] == alg)
                              & (mdf_iqr['Database'] == db)
                              & (mdf_iqr['Complexity'] == 'All')]
            if sub_all.empty:
                continue
            ax.plot(sub_all['Budget'], sub_all['IQR_log'],
                    marker=M_MARKER[db], linewidth=2.5,
                    linestyle=M_LINESTYLE[db],
                    color=M_PALETTE[db], label=f'{db} (all queries)', zorder=5)
        for complexity in COMPLEXITY_ORDER:
            ax.plot([], [], color=COMPLEXITY_COLORS[complexity],
                    linewidth=2, alpha=0.55, label=complexity)
        ax.set_title(ALG_LABEL[alg], fontsize=12)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('IQR of log₁₀(Speedup)  [log units]')
        ax.legend(fontsize=8.5, ncol=2, loc='upper left')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        ax.text(0.98, 0.97, 'Higher = more variance\nLower = more consistent',
                transform=ax.transAxes, ha='right', va='top', fontsize=8, color='grey',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc', alpha=0.8))
    add_equation(fig,
        r'IQR_log(budget) = Q75( log10(Speedup_i(budget)) ) - Q25( log10(Speedup_i(budget)) )',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig('benchmark_results/charts/proof_M5_variance_stability.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr


# ═════════════════════════════════════════════════
# PLOT M6 — SPEEDUP DISTRIBUTION AT SELECTED BUDGETS  (all 3 DBs)
# ═════════════════════════════════════════════════

old_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
try:
    for alg in ALG_ORDER:
        fig, axes = plt.subplots(
            4, 3, figsize=(14, 18), sharey=False,
            gridspec_kw={'hspace': 0.45, 'wspace': 0.32}
        )
        fig.suptitle(
            f'Plot M6  —  Speedup Distribution at Selected Budgets  ·  {ALG_LABEL[alg]}  (all 3 DBs)\n'
            'Each row = one budget level  ·  columns = complexity tier  ·  log scale',
            fontsize=13, fontweight='bold', y=1.01
        )
        for row_i, budget in enumerate(SELECTED_BUDGETS):
            df_b = mdf[(mdf['Budget'] == budget) & (mdf['Algorithm'] == alg)].copy()
            for col_i, complexity in enumerate(COMPLEXITY_ORDER):
                ax = axes[row_i][col_i]
                df_bc = df_b[df_b['Complexity'] == complexity]
                sns.boxplot(
                    data=df_bc,
                    x='Database', y='Speedup',
                    order=['POSTGRES', 'DB2', 'MYSQL'],
                    palette=M_PALETTE,
                    width=0.5, linewidth=1.2,
                    flierprops=dict(marker='o', markersize=3, alpha=0.45, linestyle='none'),
                    ax=ax
                )
                ax.set_yscale('log')
                ax.axhline(1.0, color='grey', linewidth=1, linestyle='--', alpha=0.65)
                ax.set_xlabel('')
                ax.set_ylabel('Speedup  (log)' if col_i == 0 else '')
                if row_i == 0:
                    n_q = df_bc['Query'].nunique()
                    ax.set_title(f'{complexity}\nn={n_q} queries',
                                 fontsize=10, fontweight='bold',
                                 color=COMPLEXITY_COLORS[complexity])
                else:
                    ax.set_title('')
                if col_i == 0:
                    ax.set_ylabel(f'{BUDGET_LABELS[budget]}\n\nSpeedup  (log)', fontsize=9)
        add_equation(fig,
            r'Speedup_i(b) = t_i(no-index) / t_i(indexed, b)     >1 = faster,  =1 = no change,  <1 = regression',
            y=-0.01)
        plt.savefig(f'benchmark_results/charts/proof_M6_speedup_dist_{alg}.png', bbox_inches='tight')
finally:
    sys.stderr.close()
    sys.stderr = old_stderr
