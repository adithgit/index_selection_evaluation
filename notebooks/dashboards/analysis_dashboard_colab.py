# ════════════════════════════════════════════════════════════════════
# CELL 1 — Install dependencies  (run once, then restart runtime)
# ════════════════════════════════════════════════════════════════════
# !pip install -q scipy seaborn pandas matplotlib


# ════════════════════════════════════════════════════════════════════
# CELL 2 — Mount Google Drive  (skip if uploading files directly)
# ════════════════════════════════════════════════════════════════════
# from google.colab import drive
# drive.mount('/content/drive')

# Folder where you uploaded the CSV files directly
BASE_PATH = '/content'   # default Colab upload location


# ════════════════════════════════════════════════════════════════════
# CELL 3 — Imports & shared helpers
# ════════════════════════════════════════════════════════════════════
import os, json, warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import seaborn as sns
from scipy.stats import gmean

warnings.filterwarnings('ignore')


def parse_dict(s):
    s = str(s).replace("'", '"').replace('None', 'null')
    return json.loads(s)


def csv_path(algo, db):
    return os.path.join(BASE_PATH, f'results_{algo}_JOB_REFINED_{db}_33_queries.csv')


def get_baseline_runtimes(path):
    try:
        df = pd.read_csv(path, sep=';')
        row = df.iloc[-1]
        runtimes = {}
        for col in df.columns:
            if col.endswith('.sql'):
                try:
                    val = parse_dict(row[col])
                    if val.get('Runtimes') and val['Runtimes'][0] is not None:
                        runtimes[col] = val['Runtimes'][0]
                except Exception:
                    pass
        return runtimes
    except Exception as e:
        print(f"Failed to read {path}: {e}")
        return {}


def get_indexed_runtimes(path, budget):
    try:
        df = pd.read_csv(path, sep=';')
        row = None
        for _, r in df.iterrows():
            if pd.isna(r['parameters']):
                continue
            try:
                p = json.loads(str(r['parameters']).replace("'", '"'))
                b = p.get('budget_MB')
                if b == budget or (isinstance(b, list) and budget in b):
                    row = r  # always keep most recent match
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
                except Exception:
                    pass
        return runtimes
    except Exception as e:
        print(f"Failed to read {path}: {e}")
        return {}


def build_records(db_list, budget_list, baselines):
    records = []
    for db in db_list:
        for q, rt in baselines[db].items():
            cplx = query_complexity.get(q, 'Complex')
            for alg in algorithms:
                records.append({'Budget': 0, 'Algorithm': alg.capitalize(),
                                'Database': db.upper(), 'Query': q,
                                'Complexity': cplx, 'Runtime': rt, 'Speedup': 1.0})
    for b in budget_list:
        for db in db_list:
            for alg in algorithms:
                indexed = get_indexed_runtimes(csv_path(alg, db), b)
                for q, base_rt in baselines[db].items():
                    idx_rt = indexed.get(q) if indexed else None
                    if not idx_rt or idx_rt <= 0:
                        idx_rt = base_rt   # impute missing with baseline
                    cplx = query_complexity.get(q, 'Complex')
                    records.append({'Budget': b, 'Algorithm': alg.capitalize(),
                                    'Database': db.upper(), 'Query': q,
                                    'Complexity': cplx, 'Runtime': idx_rt,
                                    'Speedup': base_rt / idx_rt})
    out = pd.DataFrame(records)
    out['Complexity'] = pd.Categorical(
        out['Complexity'], categories=['Simple', 'Intermediate', 'Complex'], ordered=True)
    return out


def build_aggregates(df):
    agg_runtime = (df.groupby(['Budget', 'Algorithm', 'Database'])['Runtime']
                   .agg(Arithmetic=np.mean, Geometric=gmean).reset_index())
    agg_runtime_cat = (df.groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Runtime']
                       .agg(Geometric=gmean).reset_index())
    agg_gmsr = (df[df['Budget'] > 0]
                .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
                .apply(gmean).reset_index(name='GMSR'))
    agg_robust = (df[df['Budget'] > 0]
                  .groupby(['Budget', 'Algorithm', 'Database'])['Speedup']
                  .agg(Trimmed=trimmed_mean).reset_index())
    agg_gmsr_cat = (df[df['Budget'] > 0]
                    .groupby(['Budget', 'Algorithm', 'Database', 'Complexity'])['Speedup']
                    .apply(gmean).reset_index(name='GMSR'))
    return agg_runtime, agg_runtime_cat, agg_gmsr, agg_robust, agg_gmsr_cat


def build_iqr(df, db_list):
    rows = []
    for b in df['Budget'].unique():
        if b == 0:
            continue
        for alg in ALG_ORDER:
            for db in db_list:
                sub = df[(df['Budget'] == b) & (df['Algorithm'] == alg)
                         & (df['Database'] == db)]['Speedup'].dropna().values
                sub = sub[sub > 0]
                if len(sub) < 4:
                    continue
                log_s = np.log10(sub)
                rows.append({'Budget': b, 'Algorithm': alg, 'Database': db,
                             'Complexity': 'All',
                             'IQR_log': float(np.percentile(log_s, 75) - np.percentile(log_s, 25)),
                             'Median_log': float(np.median(log_s))})
                for complexity in COMPLEXITY_ORDER:
                    sub_c = df[(df['Budget'] == b) & (df['Algorithm'] == alg)
                               & (df['Database'] == db)
                               & (df['Complexity'] == complexity)]['Speedup'].dropna().values
                    sub_c = sub_c[sub_c > 0]
                    if len(sub_c) < 2:
                        continue
                    log_c = np.log10(sub_c)
                    rows.append({'Budget': b, 'Algorithm': alg, 'Database': db,
                                 'Complexity': complexity,
                                 'IQR_log': float(np.percentile(log_c, 75) - np.percentile(log_c, 25)),
                                 'Median_log': float(np.median(log_c))})
    return pd.DataFrame(rows)


def trimmed_mean(x):
    n = len(x)
    return np.mean(np.sort(x)[3:-3]) if n > 6 else np.mean(x)


def add_equation(fig, equation, y=0.01, fontsize=10):
    fig.text(0.5, y, equation, ha='center', va='bottom',
             fontsize=fontsize, color='#444444', fontfamily='monospace',
             bbox=dict(boxstyle='round,pad=0.4', fc='#F5F5F5', ec='#CCCCCC', alpha=0.9))


# ════════════════════════════════════════════════════════════════════
# CELL 4 — Shared config
# ════════════════════════════════════════════════════════════════════

algorithms = ['extend', 'db2advis']
ALG_ORDER  = ['Extend', 'Db2advis']
ALG_LABEL  = {'Extend': 'Extend', 'Db2advis': 'db2advis'}

COMPLEXITY_COLORS = {'Simple': '#2196F3', 'Intermediate': '#FF9800', 'Complex': '#9C27B0'}
COMPLEXITY_ORDER  = ['Simple', 'Intermediate', 'Complex']

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

sns.set_theme(style='whitegrid', font_scale=1.05)
SELECTED_BUDGETS = [250, 3000, 7500, 15000]
BUDGET_LABELS    = {250: '250 MB', 3000: '3,000 MB', 7500: '7,500 MB', 15000: '15,000 MB'}


# ════════════════════════════════════════════════════════════════════════════
#  ███████╗███████╗ ██████╗████████╗██╗ ██████╗ ███╗   ██╗     ██╗
#  ██╔════╝██╔════╝██╔════╝╚══██╔══╝██║██╔═══██╗████╗  ██║    ███║
#  ███████╗█████╗  ██║        ██║   ██║██║   ██║██╔██╗ ██║    ╚██║
#  ╚════██║██╔══╝  ██║        ██║   ██║██║   ██║██║╚██╗██║     ██║
#  ███████║███████╗╚██████╗   ██║   ██║╚██████╔╝██║ ╚████║     ██║
#  ╚══════╝╚══════╝ ╚═════╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝     ╚═╝
#
#  POSTGRES + DB2  (full 15-budget range)
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# CELL 5 — Load data  (Postgres + DB2)
# ════════════════════════════════════════════════════════════════════

S1_DBS     = ['postgres', 'db2']
S1_BUDGETS = [250, 500, 1000, 1750, 2500, 3000, 3500, 4000, 4500,
              5000, 6250, 7500, 10000, 12500, 15000]
S1_PALETTE   = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8'}
S1_LINESTYLE = {'POSTGRES': '-', 'DB2': '--'}
S1_MARKER    = {'POSTGRES': 'o', 'DB2': 's'}

s1_baselines = {db: get_baseline_runtimes(csv_path('no_index', db)) for db in S1_DBS}
s1_df = build_records(S1_DBS, S1_BUDGETS, s1_baselines)
s1_agg_rt, s1_agg_rt_cat, s1_agg_gmsr, s1_agg_robust, s1_agg_gmsr_cat = build_aggregates(s1_df)
s1_iqr = build_iqr(s1_df, [d.upper() for d in S1_DBS])
print(f"Section 1 loaded — {len(S1_DBS)} DBs × {len(S1_BUDGETS)} budgets")


# ════════════════════════════════════════════════════════════════════
# CELL 6 — [S1] Plot 1: Arithmetic Mean Runtime
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S1]  Arithmetic Mean Query Runtime vs. Index Budget  —  Postgres + DB2',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s1_agg_rt[s1_agg_rt['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Arithmetic'],
                marker=S1_MARKER[db], linewidth=2.3,
                linestyle=S1_LINESTYLE[db], color=S1_PALETTE[db], label=db)
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Arithmetic Mean Runtime (ms)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig,
    r'AM(budget) = (1/n) · Σ t_i(budget)     n = 33 queries', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 7 — [S1] Plot 2: Geometric Mean Runtime
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S1]  Geometric Mean Query Runtime vs. Index Budget  —  Postgres + DB2',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s1_agg_rt[s1_agg_rt['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Geometric'],
                marker=S1_MARKER[db], linewidth=2.3,
                linestyle=S1_LINESTYLE[db], color=S1_PALETTE[db], label=db)
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Geometric Mean Runtime (ms)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig,
    r'GM(budget) = exp( (1/n) · Σ ln(t_i(budget)) )     log-appropriate for skewed runtimes',
    y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 8 — [S1] Plot 2b: Geometric Mean Runtime by Complexity
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle(
        f'[S1]  Geometric Mean Runtime by Complexity  ·  {ALG_LABEL[alg]}  —  Postgres + DB2\n'
        'Simple (≤6 joins)  ·  Intermediate (7-11)  ·  Complex (≥12)',
        fontsize=13, fontweight='bold', y=1.04)
    sub_alg = s1_agg_rt_cat[s1_agg_rt_cat['Algorithm'] == alg]
    for ax, complexity in zip(axes, COMPLEXITY_ORDER):
        sub = sub_alg[sub_alg['Complexity'] == complexity]
        col = COMPLEXITY_COLORS[complexity]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Geometric'],
                    marker=S1_MARKER[db], linewidth=2.2,
                    linestyle=S1_LINESTYLE[db], color=col, label=db,
                    alpha=0.9 if db == 'POSTGRES' else 0.65)
        ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Geometric Mean Runtime (ms)')
        ax.legend(title='Database', fontsize=9)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        n_q = s1_df[s1_df['Complexity'] == complexity]['Query'].nunique()
        ax.text(0.97, 0.04, f'n = {n_q} queries', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8.5, color='grey')
    add_equation(fig,
        r'GM_c(budget) = exp( (1/n_c) · Σ_{i∈c} ln(t_i(budget)) )', y=-0.04)
    plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 9 — [S1] Plot 3a: Trimmed Mean Speedup
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S1]  Trimmed Mean Speedup (10%) vs. Index Budget  —  Postgres + DB2\n'
             'Removes top/bottom 10% extreme speedups',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s1_agg_robust[s1_agg_robust['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Trimmed'],
                marker=S1_MARKER[db], linewidth=2.3,
                linestyle=S1_LINESTYLE[db], label=db, color=S1_PALETTE[db])
    ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Trimmed Mean Speedup')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig, r'TrimmedMean = mean( speedups excluding top/bottom 10% )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 10 — [S1] Plot 4: GMSR
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S1]  Geometric Mean Speedup Ratio (GMSR) vs. Index Budget  —  Postgres + DB2\n'
             'Primary DB-agnostic metric  ·  GMSR > 1 means indexes help',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s1_agg_gmsr[s1_agg_gmsr['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['GMSR'],
                marker=S1_MARKER[db], linewidth=2.3,
                linestyle=S1_LINESTYLE[db], label=db, color=S1_PALETTE[db])
    ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('GMSR  (higher = better)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    if alg == 'Extend':
        ax.annotate('DB2: GMSR ≈ 1×\n(engine does not\nexploit indexes)',
                    xy=(6000, 1.06), fontsize=8.5, color='#1B6CA8',
                    bbox=dict(boxstyle='round,pad=0.35', fc='#EBF3FB', ec='#1B6CA8', alpha=0.92))
add_equation(fig,
    r'GMSR(budget) = exp( (1/n) · Σ ln( t_i(no-index) / t_i(indexed) ) )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 11 — [S1] Plot 4b: GMSR by Complexity
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle(
        f'[S1]  GMSR by Query Complexity  ·  {ALG_LABEL[alg]}  —  Postgres + DB2\n'
        'Simple (≤6 joins)  ·  Intermediate (7-11)  ·  Complex (≥12)',
        fontsize=13, fontweight='bold', y=1.04)
    sub_alg = s1_agg_gmsr_cat[s1_agg_gmsr_cat['Algorithm'] == alg]
    for ax, complexity in zip(axes, COMPLEXITY_ORDER):
        sub = sub_alg[sub_alg['Complexity'] == complexity]
        col = COMPLEXITY_COLORS[complexity]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['GMSR'],
                    marker=S1_MARKER[db], linewidth=2.2,
                    linestyle=S1_LINESTYLE[db], color=col, label=db,
                    alpha=0.9 if db == 'POSTGRES' else 0.65)
        ax.axhline(1.0, color='grey', linewidth=1, linestyle=':', alpha=0.6)
        ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('GMSR  (higher = better)')
        ax.legend(title='Database', fontsize=9)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        n_q = s1_df[s1_df['Complexity'] == complexity]['Query'].nunique()
        ax.text(0.97, 0.04, f'n = {n_q} queries', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8.5, color='grey')
    add_equation(fig,
        r'GMSR_c(budget) = exp( (1/n_c) · Σ_{i∈c} ln( t_i(no-index) / t_i(indexed) ) )',
        y=-0.04)
    plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 12 — [S1] Plot 5: Speedup IQR Width
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S1]  Speedup IQR Width vs. Index Budget  —  Postgres + DB2\n'
             'Shrinking = more consistent  ·  Flat/growing = persistent winners and losers',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    for complexity in COMPLEXITY_ORDER:
        col = COMPLEXITY_COLORS[complexity]
        for db in [d.upper() for d in S1_DBS]:
            sub = s1_iqr[(s1_iqr['Algorithm'] == alg) & (s1_iqr['Database'] == db)
                         & (s1_iqr['Complexity'] == complexity)]
            if not sub.empty:
                ax.plot(sub['Budget'], sub['IQR_log'], linewidth=1.1,
                        linestyle=S1_LINESTYLE[db], color=col, alpha=0.30)
    for db in [d.upper() for d in S1_DBS]:
        sub_all = s1_iqr[(s1_iqr['Algorithm'] == alg) & (s1_iqr['Database'] == db)
                         & (s1_iqr['Complexity'] == 'All')]
        if not sub_all.empty:
            ax.plot(sub_all['Budget'], sub_all['IQR_log'],
                    marker=S1_MARKER[db], linewidth=2.5,
                    linestyle=S1_LINESTYLE[db], color=S1_PALETTE[db],
                    label=f'{db} (all queries)', zorder=5)
    for complexity in COMPLEXITY_ORDER:
        ax.plot([], [], color=COMPLEXITY_COLORS[complexity], linewidth=2, alpha=0.55, label=complexity)
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('IQR of log₁₀(Speedup)')
    ax.legend(fontsize=8.5, ncol=2, loc='upper left')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.text(0.98, 0.97, 'Higher = more variance\nLower = more consistent',
            transform=ax.transAxes, ha='right', va='top', fontsize=8, color='grey',
            bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc', alpha=0.8))
add_equation(fig,
    r'IQR_log = Q75( log₁₀(Speedup_i) ) - Q25( log₁₀(Speedup_i) )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 13 — [S1] Plot 6: Speedup Distribution at Selected Budgets
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(4, 3, figsize=(14, 18), sharey=False,
                             gridspec_kw={'hspace': 0.45, 'wspace': 0.32})
    fig.suptitle(
        f'[S1]  Speedup Distribution at Selected Budgets  ·  {ALG_LABEL[alg]}  —  Postgres + DB2\n'
        'Each row = one budget  ·  columns = complexity tier  ·  log scale',
        fontsize=13, fontweight='bold', y=1.01)
    for row_i, budget in enumerate(SELECTED_BUDGETS):
        df_b = s1_df[(s1_df['Budget'] == budget) & (s1_df['Algorithm'] == alg)].copy()
        for col_i, complexity in enumerate(COMPLEXITY_ORDER):
            ax = axes[row_i][col_i]
            df_bc = df_b[df_b['Complexity'] == complexity]
            sns.boxplot(data=df_bc, x='Database', y='Speedup',
                        order=['POSTGRES', 'DB2'], palette=S1_PALETTE,
                        width=0.5, linewidth=1.2,
                        flierprops=dict(marker='o', markersize=3, alpha=0.45, linestyle='none'),
                        ax=ax)
            ax.set_yscale('log')
            ax.axhline(1.0, color='grey', linewidth=1, linestyle='--', alpha=0.65)
            ax.set_xlabel('')
            if row_i == 0:
                n_q = df_bc['Query'].nunique()
                ax.set_title(f'{complexity}\nn={n_q} queries', fontsize=10,
                             fontweight='bold', color=COMPLEXITY_COLORS[complexity])
            else:
                ax.set_title('')
            ax.set_ylabel(
                (f'{BUDGET_LABELS[budget]}\n\nSpeedup  (log)' if col_i == 0 else ''))
    add_equation(fig,
        r'Speedup_i(b) = t_i(no-index) / t_i(indexed, b)     >1=faster  <1=regression',
        y=-0.01)
    plt.show()


# ════════════════════════════════════════════════════════════════════════════
#  ███████╗███████╗ ██████╗████████╗██╗ ██████╗ ███╗   ██╗    ██████╗
#  ██╔════╝██╔════╝██╔════╝╚══██╔══╝██║██╔═══██╗████╗  ██║    ╚════██╗
#  ███████╗█████╗  ██║        ██║   ██║██║   ██║██╔██╗ ██║     █████╔╝
#  ╚════██║██╔══╝  ██║        ██║   ██║██║   ██║██║╚██╗██║    ██╔═══╝
#  ███████║███████╗╚██████╗   ██║   ██║╚██████╔╝██║ ╚████║    ███████╗
#  ╚══════╝╚══════╝ ╚═════╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝    ╚══════╝
#
#  POSTGRES + DB2 + MYSQL  (budgets where MySQL has real data)
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# CELL 14 — Load data  (all 3 DBs, MySQL-available budgets only)
# ════════════════════════════════════════════════════════════════════

S2_DBS     = ['postgres', 'db2', 'mysql']
# Only budgets where MySQL has actual benchmark results (no imputation needed)
S2_BUDGETS = [250, 500, 1000, 2500, 5000, 7500, 10000, 15000]
S2_PALETTE   = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8', 'MYSQL': '#2E7D32'}
S2_LINESTYLE = {'POSTGRES': '-',       'DB2': '--',       'MYSQL': ':'}
S2_MARKER    = {'POSTGRES': 'o',       'DB2': 's',        'MYSQL': '^'}
S2_ALPHA     = {'POSTGRES': 0.9,       'DB2': 0.65,       'MYSQL': 0.8}

# Selected budgets for distribution plots — must exist in S2_BUDGETS
S2_SELECTED_BUDGETS = [250, 2500, 7500, 15000]
S2_BUDGET_LABELS    = {250: '250 MB', 2500: '2,500 MB', 7500: '7,500 MB', 15000: '15,000 MB'}

s2_baselines = {db: get_baseline_runtimes(csv_path('no_index', db)) for db in S2_DBS}
s2_df = build_records(S2_DBS, S2_BUDGETS, s2_baselines)
s2_agg_rt, s2_agg_rt_cat, s2_agg_gmsr, s2_agg_robust, s2_agg_gmsr_cat = build_aggregates(s2_df)
s2_iqr = build_iqr(s2_df, [d.upper() for d in S2_DBS])
print(f"Section 2 loaded — {len(S2_DBS)} DBs × {len(S2_BUDGETS)} budgets (MySQL-available only)")


# ════════════════════════════════════════════════════════════════════
# CELL 15 — [S2] Plot 1: Arithmetic Mean Runtime
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S2]  Arithmetic Mean Query Runtime vs. Index Budget  —  Postgres + DB2 + MySQL',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s2_agg_rt[s2_agg_rt['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Arithmetic'],
                marker=S2_MARKER[db], linewidth=2.3,
                linestyle=S2_LINESTYLE[db], color=S2_PALETTE[db],
                label=db, alpha=S2_ALPHA[db])
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Arithmetic Mean Runtime (ms)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig,
    r'AM(budget) = (1/n) · Σ t_i(budget)     n = 33 queries', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 16 — [S2] Plot 2: Geometric Mean Runtime
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S2]  Geometric Mean Query Runtime vs. Index Budget  —  Postgres + DB2 + MySQL',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s2_agg_rt[s2_agg_rt['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Geometric'],
                marker=S2_MARKER[db], linewidth=2.3,
                linestyle=S2_LINESTYLE[db], color=S2_PALETTE[db],
                label=db, alpha=S2_ALPHA[db])
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Geometric Mean Runtime (ms)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig,
    r'GM(budget) = exp( (1/n) · Σ ln(t_i(budget)) )     log-appropriate for skewed runtimes',
    y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 17 — [S2] Plot 2b: Geometric Mean Runtime by Complexity
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle(
        f'[S2]  Geometric Mean Runtime by Complexity  ·  {ALG_LABEL[alg]}  —  Postgres + DB2 + MySQL\n'
        'Simple (≤6 joins)  ·  Intermediate (7-11)  ·  Complex (≥12)',
        fontsize=13, fontweight='bold', y=1.04)
    sub_alg = s2_agg_rt_cat[s2_agg_rt_cat['Algorithm'] == alg]
    for ax, complexity in zip(axes, COMPLEXITY_ORDER):
        sub = sub_alg[sub_alg['Complexity'] == complexity]
        col = COMPLEXITY_COLORS[complexity]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['Geometric'],
                    marker=S2_MARKER[db], linewidth=2.2,
                    linestyle=S2_LINESTYLE[db], color=col,
                    label=db, alpha=S2_ALPHA[db])
        ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('Geometric Mean Runtime (ms)')
        ax.legend(title='Database', fontsize=9)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        n_q = s2_df[s2_df['Complexity'] == complexity]['Query'].nunique()
        ax.text(0.97, 0.04, f'n = {n_q} queries', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8.5, color='grey')
    add_equation(fig,
        r'GM_c(budget) = exp( (1/n_c) · Σ_{i∈c} ln(t_i(budget)) )', y=-0.04)
    plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 18 — [S2] Plot 3a: Trimmed Mean Speedup
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S2]  Trimmed Mean Speedup (10%) vs. Index Budget  —  Postgres + DB2 + MySQL\n'
             'Removes top/bottom 10% extreme speedups',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s2_agg_robust[s2_agg_robust['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['Trimmed'],
                marker=S2_MARKER[db], linewidth=2.3,
                linestyle=S2_LINESTYLE[db], label=db,
                color=S2_PALETTE[db], alpha=S2_ALPHA[db])
    ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('Trimmed Mean Speedup')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig, r'TrimmedMean = mean( speedups excluding top/bottom 10% )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 19 — [S2] Plot 4: GMSR
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S2]  Geometric Mean Speedup Ratio (GMSR) vs. Index Budget  —  Postgres + DB2 + MySQL\n'
             'Primary DB-agnostic metric  ·  GMSR > 1 means indexes help',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    sub = s2_agg_gmsr[s2_agg_gmsr['Algorithm'] == alg]
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['GMSR'],
                marker=S2_MARKER[db], linewidth=2.3,
                linestyle=S2_LINESTYLE[db], label=db,
                color=S2_PALETTE[db], alpha=S2_ALPHA[db])
    ax.axhline(1.0, color='grey', linewidth=1.2, linestyle=':', alpha=0.7, label='No gain (1×)')
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('GMSR  (higher = better)')
    ax.legend(title='Database')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
add_equation(fig,
    r'GMSR(budget) = exp( (1/n) · Σ ln( t_i(no-index) / t_i(indexed) ) )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 20 — [S2] Plot 4b: GMSR by Complexity
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle(
        f'[S2]  GMSR by Query Complexity  ·  {ALG_LABEL[alg]}  —  Postgres + DB2 + MySQL\n'
        'Simple (≤6 joins)  ·  Intermediate (7-11)  ·  Complex (≥12)',
        fontsize=13, fontweight='bold', y=1.04)
    sub_alg = s2_agg_gmsr_cat[s2_agg_gmsr_cat['Algorithm'] == alg]
    for ax, complexity in zip(axes, COMPLEXITY_ORDER):
        sub = sub_alg[sub_alg['Complexity'] == complexity]
        col = COMPLEXITY_COLORS[complexity]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['GMSR'],
                    marker=S2_MARKER[db], linewidth=2.2,
                    linestyle=S2_LINESTYLE[db], color=col,
                    label=db, alpha=S2_ALPHA[db])
        ax.axhline(1.0, color='grey', linewidth=1, linestyle=':', alpha=0.6)
        ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('GMSR  (higher = better)')
        ax.legend(title='Database', fontsize=9)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        n_q = s2_df[s2_df['Complexity'] == complexity]['Query'].nunique()
        ax.text(0.97, 0.04, f'n = {n_q} queries', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8.5, color='grey')
    add_equation(fig,
        r'GMSR_c(budget) = exp( (1/n_c) · Σ_{i∈c} ln( t_i(no-index) / t_i(indexed) ) )',
        y=-0.04)
    plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 21 — [S2] Plot 5: Speedup IQR Width
# ════════════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
fig.suptitle('[S2]  Speedup IQR Width vs. Index Budget  —  Postgres + DB2 + MySQL\n'
             'Shrinking = more consistent  ·  Flat/growing = persistent winners and losers',
             fontsize=13, fontweight='bold', y=1.04)
for ax, alg in zip(axes, ALG_ORDER):
    for complexity in COMPLEXITY_ORDER:
        col = COMPLEXITY_COLORS[complexity]
        for db in [d.upper() for d in S2_DBS]:
            sub = s2_iqr[(s2_iqr['Algorithm'] == alg) & (s2_iqr['Database'] == db)
                         & (s2_iqr['Complexity'] == complexity)]
            if not sub.empty:
                ax.plot(sub['Budget'], sub['IQR_log'], linewidth=1.1,
                        linestyle=S2_LINESTYLE[db], color=col, alpha=0.30)
    for db in [d.upper() for d in S2_DBS]:
        sub_all = s2_iqr[(s2_iqr['Algorithm'] == alg) & (s2_iqr['Database'] == db)
                         & (s2_iqr['Complexity'] == 'All')]
        if not sub_all.empty:
            ax.plot(sub_all['Budget'], sub_all['IQR_log'],
                    marker=S2_MARKER[db], linewidth=2.5,
                    linestyle=S2_LINESTYLE[db], color=S2_PALETTE[db],
                    label=f'{db} (all queries)', zorder=5)
    for complexity in COMPLEXITY_ORDER:
        ax.plot([], [], color=COMPLEXITY_COLORS[complexity], linewidth=2, alpha=0.55, label=complexity)
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('IQR of log₁₀(Speedup)')
    ax.legend(fontsize=8.5, ncol=2, loc='upper left')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.text(0.98, 0.97, 'Higher = more variance\nLower = more consistent',
            transform=ax.transAxes, ha='right', va='top', fontsize=8, color='grey',
            bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc', alpha=0.8))
add_equation(fig,
    r'IQR_log = Q75( log₁₀(Speedup_i) ) - Q25( log₁₀(Speedup_i) )', y=-0.04)
plt.tight_layout(); plt.subplots_adjust(bottom=0.14); plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 22 — [S2] Plot 6: Speedup Distribution at Selected Budgets
# ════════════════════════════════════════════════════════════════════

for alg in ALG_ORDER:
    fig, axes = plt.subplots(4, 3, figsize=(14, 18), sharey=False,
                             gridspec_kw={'hspace': 0.45, 'wspace': 0.32})
    fig.suptitle(
        f'[S2]  Speedup Distribution at Selected Budgets  ·  {ALG_LABEL[alg]}  —  Postgres + DB2 + MySQL\n'
        'Each row = one budget  ·  columns = complexity tier  ·  log scale',
        fontsize=13, fontweight='bold', y=1.01)
    for row_i, budget in enumerate(S2_SELECTED_BUDGETS):
        df_b = s2_df[(s2_df['Budget'] == budget) & (s2_df['Algorithm'] == alg)].copy()
        for col_i, complexity in enumerate(COMPLEXITY_ORDER):
            ax = axes[row_i][col_i]
            df_bc = df_b[df_b['Complexity'] == complexity]
            sns.boxplot(data=df_bc, x='Database', y='Speedup',
                        order=['POSTGRES', 'DB2', 'MYSQL'], palette=S2_PALETTE,
                        width=0.5, linewidth=1.2,
                        flierprops=dict(marker='o', markersize=3, alpha=0.45, linestyle='none'),
                        ax=ax)
            ax.set_yscale('log')
            ax.axhline(1.0, color='grey', linewidth=1, linestyle='--', alpha=0.65)
            ax.set_xlabel('')
            if row_i == 0:
                n_q = df_bc['Query'].nunique()
                ax.set_title(f'{complexity}\nn={n_q} queries', fontsize=10,
                             fontweight='bold', color=COMPLEXITY_COLORS[complexity])
            else:
                ax.set_title('')
            ax.set_ylabel(
                (f'{S2_BUDGET_LABELS[budget]}\n\nSpeedup  (log)' if col_i == 0 else ''))
    add_equation(fig,
        r'Speedup_i(b) = t_i(no-index) / t_i(indexed, b)     >1=faster  <1=regression',
        y=-0.01)
    plt.show()


# ════════════════════════════════════════════════════════════════════════════
#  ███████╗███████╗ ██████╗████████╗██╗ ██████╗ ███╗   ██╗    ██████╗
#  ██╔════╝██╔════╝██╔════╝╚══██╔══╝██║██╔═══██╗████╗  ██║    ╚════██╗
#  ███████╗█████╗  ██║        ██║   ██║██║   ██║██╔██╗ ██║        ███╗
#  ╚════██║██╔══╝  ██║        ██║   ██║██║   ██║██║╚██╗██║    ██   ██║
#  ███████║███████╗╚██████╗   ██║   ██║╚██████╔╝██║ ╚████║    ╚█████╔╝
#  ╚══════╝╚══════╝ ╚═════╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝     ╚════╝
#
#  SUMMARY — All DBs · All Budgets · Single graph per algorithm
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# CELL 23 — [S3] GMSR summary: all budgets, all DBs, one graph per algo
#
# Postgres + DB2 use the full 15-budget range (S1_BUDGETS).
# MySQL uses only its 8 available budgets (S2_BUDGETS).
# All series share the same x-axis; missing MySQL points are simply absent.
# ════════════════════════════════════════════════════════════════════

S3_PALETTE    = {'POSTGRES': '#E8302A', 'DB2': '#1B6CA8', 'MYSQL': '#2E7D32'}
S3_LINESTYLE  = {'POSTGRES': '-',       'DB2': '--',       'MYSQL': ':'}
S3_MARKER     = {'POSTGRES': 'o',       'DB2': 's',        'MYSQL': '^'}
S3_MARKERSIZE = {'POSTGRES': 6,         'DB2': 6,           'MYSQL': 7}

# Re-use already-computed aggregates: s1_agg_gmsr (Postgres+DB2, 15 budgets)
# and s2_agg_gmsr (all 3 DBs, 8 budgets).  Pull MySQL from s2, Postgres/DB2 from s1.
_pg_db2 = s1_agg_gmsr[s1_agg_gmsr['Database'].isin(['POSTGRES', 'DB2'])].copy()
_mysql  = s2_agg_gmsr[s2_agg_gmsr['Database'] == 'MYSQL'].copy()
s3_agg_gmsr = pd.concat([_pg_db2, _mysql], ignore_index=True)

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
fig.suptitle(
    '[S3]  GMSR vs. Index Budget — All Databases, All Budgets\n'
    'Postgres & DB2: full 15-budget range  ·  MySQL: 8-budget range',
    fontsize=13, fontweight='bold', y=1.04)

for ax, alg in zip(axes, ALG_ORDER):
    sub = s3_agg_gmsr[s3_agg_gmsr['Algorithm'] == alg].sort_values('Budget')
    for db, grp in sub.groupby('Database'):
        ax.plot(grp['Budget'], grp['GMSR'],
                marker=S3_MARKER[db], markersize=S3_MARKERSIZE[db],
                linewidth=2.2, linestyle=S3_LINESTYLE[db],
                color=S3_PALETTE[db], label=db, zorder=3)
    ax.axhline(1.0, color='grey', linewidth=1.1, linestyle=':', alpha=0.65,
               label='No gain (1×)', zorder=2)
    ax.set_title(ALG_LABEL[alg], fontsize=12)
    ax.set_xlabel('Index Budget (MB)')
    ax.set_ylabel('GMSR  (higher = better)')
    ax.legend(title='Database', fontsize=9)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

add_equation(fig,
    r'GMSR(budget) = exp( (1/n) · Σ ln( t_i(no-index) / t_i(indexed) ) )     '
    r'>1 = indexes help  ·  <1 = regression',
    y=-0.04)
plt.tight_layout()
plt.subplots_adjust(bottom=0.14)
plt.savefig('gmsr_summary_all_budgets.pdf', bbox_inches='tight')
plt.show()


# ════════════════════════════════════════════════════════════════════
# CELL 24 — [S3] GMSR by complexity: all budgets, all DBs, one figure per algo
#
# Same layout as S1/S2 complexity plots but combining full Postgres+DB2
# budget range with MySQL's 8-budget range in the same panel.
# ════════════════════════════════════════════════════════════════════

_pg_db2_cat = s1_agg_gmsr_cat[s1_agg_gmsr_cat['Database'].isin(['POSTGRES', 'DB2'])].copy()
_mysql_cat  = s2_agg_gmsr_cat[s2_agg_gmsr_cat['Database'] == 'MYSQL'].copy()
s3_agg_gmsr_cat = pd.concat([_pg_db2_cat, _mysql_cat], ignore_index=True)

for alg in ALG_ORDER:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle(
        f'[S3]  GMSR by Query Complexity  ·  {ALG_LABEL[alg]}  —  All Databases\n'
        'Simple (≤6 joins)  ·  Intermediate (7-11)  ·  Complex (≥12)',
        fontsize=13, fontweight='bold', y=1.04)
    sub_alg = s3_agg_gmsr_cat[s3_agg_gmsr_cat['Algorithm'] == alg].sort_values('Budget')
    for ax, complexity in zip(axes, COMPLEXITY_ORDER):
        sub = sub_alg[sub_alg['Complexity'] == complexity]
        col = COMPLEXITY_COLORS[complexity]
        for db, grp in sub.groupby('Database'):
            ax.plot(grp['Budget'], grp['GMSR'],
                    marker=S3_MARKER[db], markersize=S3_MARKERSIZE[db],
                    linewidth=2.1, linestyle=S3_LINESTYLE[db],
                    color=col, label=db,
                    alpha=0.9 if db == 'POSTGRES' else 0.7)
        ax.axhline(1.0, color='grey', linewidth=1, linestyle=':', alpha=0.6)
        ax.set_title(complexity, fontsize=12, fontweight='bold', color=col)
        ax.set_xlabel('Index Budget (MB)')
        ax.set_ylabel('GMSR  (higher = better)')
        ax.legend(title='Database', fontsize=9)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        n_q_s1 = s1_df[s1_df['Complexity'] == complexity]['Query'].nunique()
        ax.text(0.97, 0.04, f'n = {n_q_s1} queries', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8.5, color='grey')
    add_equation(fig,
        r'GMSR_c(budget) = exp( (1/n_c) · Σ_{i∈c} ln( t_i(no-index) / t_i(indexed) ) )',
        y=-0.04)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.14)
    plt.savefig(f'gmsr_complexity_{alg.lower()}_all_budgets.pdf', bbox_inches='tight')
    plt.show()
