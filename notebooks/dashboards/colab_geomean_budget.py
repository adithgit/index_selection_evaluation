# ════════════════════════════════════════════════════════════════════
# Geomean cost-speedup vs runtime-speedup across budgets
# Upload these CSVs to /content before running:
#   results_no_index_JOB_REFINED_{postgres,db2,mysql}_33_queries.csv
#   results_extend_JOB_REFINED_{postgres,db2,mysql}_33_queries.csv
#   results_db2advis_JOB_REFINED_{postgres,db2,mysql}_33_queries.csv
# ════════════════════════════════════════════════════════════════════
import os, json, math, warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

warnings.filterwarnings('ignore')

BASE_PATH = '/content'   # change if using Drive: '/content/drive/MyDrive/...'
DBS       = ['postgres', 'db2', 'mysql']
ALGOS     = ['extend', 'db2advis']

# ── helpers ──────────────────────────────────────────────────────────

def csv_path(algo, db):
    return os.path.join(BASE_PATH, f'results_{algo}_JOB_REFINED_{db}_33_queries.csv')

def parse_cell(s):
    return json.loads(str(s).replace("'", '"').replace('None', 'null'))

def query_cols(df):
    return [c for c in df.columns if c.endswith('.sql')]

def load_baseline(db):
    """Return {query: (runtime_ms, cost)} for the no-index run."""
    df = pd.read_csv(csv_path('no_index', db), sep=';', on_bad_lines='skip')
    row = df.iloc[0]
    out = {}
    for q in query_cols(df):
        try:
            v = parse_cell(row[q])
            rt = v['Runtimes'][0]
            cost = v['Cost']
            if rt is not None and cost:
                out[q] = (rt, cost)
        except Exception:
            pass
    return out

def load_indexed(algo, db):
    """Return {budget_MB: {query: (runtime_ms, cost)}} for all budget rows."""
    df = pd.read_csv(csv_path(algo, db), sep=';', on_bad_lines='skip')
    result = {}
    for _, row in df.iterrows():
        try:
            params = parse_cell(row['parameters'])
            budget = params.get('budget_MB')
            if budget is None:
                continue
        except Exception:
            continue
        qdata = {}
        for q in query_cols(df):
            try:
                v = parse_cell(row[q])
                rt = v['Runtimes'][0]
                cost = v['Cost']
                if rt is not None and cost:
                    qdata[q] = (rt, cost)
            except Exception:
                pass
        if qdata:
            result[budget] = qdata
    return result

def geomean(vals):
    vals = [v for v in vals if v > 0]
    return math.exp(sum(math.log(v) for v in vals) / len(vals)) if vals else float('nan')

def compute_geomeans(algo, db, baseline):
    indexed = load_indexed(algo, db)
    rows = []
    for budget in sorted(indexed):
        qdata = indexed[budget]
        shared = [q for q in qdata if q in baseline]
        if not shared:
            continue
        rt_speedups   = [baseline[q][0] / qdata[q][0] for q in shared]
        cost_speedups = [baseline[q][1] / qdata[q][1] for q in shared
                         if qdata[q][1] > 0 and baseline[q][1] > 0]
        rows.append({
            'budget':       budget,
            'geomean_rt':   geomean(rt_speedups),
            'geomean_cost': geomean(cost_speedups),
        })
    return rows

# ── compute ──────────────────────────────────────────────────────────

baselines = {db: load_baseline(db) for db in DBS}

data = {}   # data[algo][db] = list of {budget, geomean_rt, geomean_cost}
for algo in ALGOS:
    data[algo] = {}
    for db in DBS:
        data[algo][db] = compute_geomeans(algo, db, baselines[db])

# ── plot ─────────────────────────────────────────────────────────────

DB_STYLE = {
    'postgres': dict(color='#4e79a7', marker='o', label='PostgreSQL'),
    'db2':      dict(color='#f28e2b', marker='s', label='DB2'),
    'mysql':    dict(color='#e15759', marker='^', label='MySQL'),
}
ALGO_TITLE = {'extend': 'EXTEND', 'db2advis': 'DB2Advis'}

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
fig.suptitle(
    'Geometric-mean optimizer cost speedup vs actual runtime speedup\n'
    'JOB-refined · 33 queries · all index budgets',
    fontsize=13, y=1.02,
)

for ax, algo in zip(axes, ALGOS):
    for db in DBS:
        rows = data[algo][db]
        if not rows:
            continue
        xs = [r['geomean_cost'] for r in rows]
        ys = [r['geomean_rt']   for r in rows]
        budgets = [r['budget']  for r in rows]
        st = DB_STYLE[db]

        ax.plot(xs, ys, color=st['color'], marker=st['marker'],
                label=st['label'], linewidth=1.6, markersize=7, zorder=3)

        # label smallest and largest budget
        for i in (0, -1):
            label = f"{budgets[i]:,}MB" if budgets[i] < 1000 else f"{budgets[i]//1000}GB"
            ax.annotate(label, (xs[i], ys[i]),
                        textcoords='offset points', xytext=(6, 3),
                        fontsize=7.5, color=st['color'])

    # reference lines
    ax.axhline(1, color='black', linewidth=0.8, linestyle='--', alpha=0.35)
    ax.axvline(1, color='black', linewidth=0.8, linestyle='--', alpha=0.35)

    # shade the "runtime got worse" region
    xlim = ax.get_xlim()
    ax.fill_betweenx([0.01, 1], 0.01, 1e15, alpha=0.04, color='red')

    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xlabel('Geomean cost speedup  (optimizer estimate)', fontsize=10)
    ax.set_ylabel('Geomean runtime speedup  (actual)', fontsize=10)
    ax.set_title(ALGO_TITLE[algo], fontsize=11, fontweight='bold')
    ax.legend(fontsize=9)
    ax.grid(True, which='both', linestyle=':', linewidth=0.5, alpha=0.6)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f'{v:.2g}×'))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f'{v:.2g}×'))

plt.tight_layout()
plt.savefig('geomean_budget_speedup.pdf', bbox_inches='tight')
plt.show()
