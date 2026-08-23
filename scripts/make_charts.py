import pickle, math, os, csv, json
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats as st

D = pickle.load(open('/tmp/all_results.pkl','rb'))
OUT = '/tmp/figs'; os.makedirs(OUT, exist_ok=True)
BLUE, ORANGE, GREEN, GREY, RED = '#2a78d6', '#eb6834', '#1baf7a', '#898781', '#e34948'

def finish(fig, ax, name, legend=True):
    for s in ('top','right'): ax.spines[s].set_visible(False)
    ax.grid(axis='y', color='#d5d5d5', lw=.6, alpha=.7); ax.set_axisbelow(True)
    if legend: ax.legend(fontsize=9, frameon=False)
    fig.tight_layout(); fig.savefig(f'{OUT}/{name}.png', dpi=170, facecolor='white'); plt.close(fig)
    print('wrote', name)

# ---- FIG 1: speedup vs budget, per DBMS ----
fig, axes = plt.subplots(1, 3, figsize=(13.5,4.0), sharey=True)
for ax, db, title in zip(axes, ['postgres','db2','mysql'], ['PostgreSQL','IBM DB2','MySQL (VIDEX)']):
    for algo, col in [('extend',BLUE),('db2advis',ORANGE),('relaxation',GREEN)]:
        pts = D['speedup'].get(db,{}).get(algo)
        if not pts: continue
        x=[p[0] for p in pts]; y=[p[1] for p in pts]
        ax.plot(x,y,'o-',color=col,label=algo,lw=1.8,ms=4)
    ax.axhline(1.0, color=GREY, ls='--', lw=1.2)
    ax.set_xscale('log'); ax.set_title(title, fontsize=11)
    ax.set_xlabel('Index budget (MB, log)')
    for s in ('top','right'): ax.spines[s].set_visible(False)
    ax.grid(axis='y', color='#d5d5d5', lw=.6, alpha=.7); ax.set_axisbelow(True)
    ax.legend(fontsize=8, frameon=False)
axes[0].set_ylabel('Speedup vs no-index  (>1 = faster)')
fig.tight_layout(); fig.savefig(f'{OUT}/fig1_speedup.png', dpi=170, facecolor='white'); plt.close(fig)
print('wrote fig1_speedup')

# ---- FIG 2: cost vs runtime scatter per DBMS ----
BR='/Users/adithyaudayan/index_selection_evaluation/benchmark_results'
csv.field_size_limit(10**9)
FILES={'postgres':['results_extend_JOB_REFINED_postgres_33_queries.csv','results_db2advis_JOB_REFINED_postgres_33_queries.csv','results_relaxation_JOB_REFINED_postgres_33_queries.csv'],
       'db2':['results_extend_JOB_REFINED_db2_33_queries.csv','results_db2advis_JOB_REFINED_db2_33_queries.csv'],
       'mysql':['results_extend_JOB_REFINED_mysql_33_queries.csv','results_db2advis_JOB_REFINED_mysql_33_queries.csv']}
fig, axes = plt.subplots(1,3, figsize=(13.5,4.2))
for ax, db, title, col in zip(axes,['postgres','db2','mysql'],['PostgreSQL','IBM DB2','MySQL (VIDEX)'],[BLUE,ORANGE,GREEN]):
    cs,rs=[],[]
    for fn in FILES[db]:
        p=os.path.join(BR,fn)
        if not os.path.exists(p): continue
        rd=csv.reader(open(p),delimiter=';'); hdr=next(rd)
        qc=[i for i,h in enumerate(hdr) if h.endswith('.sql')]
        for r in rd:
            for i in qc:
                try:
                    d=json.loads(r[i]); rt=(d.get('Runtimes') or [None])[0]; c=d.get('Cost')
                    if rt and c and rt>0 and c>0: cs.append(c); rs.append(rt)
                except Exception: pass
    if cs:
        rho=st.spearmanr(cs,rs)[0]
        ax.scatter(rs,cs,s=7,alpha=.28,color=col,edgecolors='none')
        ax.set_xscale('log'); ax.set_yscale('log')
        ax.set_title(f'{title}\nSpearman ρ = {rho:+.3f}   (n={len(cs)})', fontsize=10)
    ax.set_xlabel('Actual runtime (ms, log)')
    for s in ('top','right'): ax.spines[s].set_visible(False)
    ax.grid(color='#d5d5d5', lw=.6, alpha=.6); ax.set_axisbelow(True)
axes[0].set_ylabel('Optimizer predicted cost (log)')
fig.tight_layout(); fig.savefig(f'{OUT}/fig2_cost_runtime.png', dpi=170, facecolor='white'); plt.close(fig)
print('wrote fig2_cost_runtime')

# ---- FIG 3: calibration, runtime vs budget ----
fig, axes = plt.subplots(2,2, figsize=(12,7.5))
combos=[('JOB','relaxation'),('JOB','db2advis'),('TPCH','relaxation'),('TPCH','db2advis')]
for ax,(wl,algo) in zip(axes.flat, combos):
    d=D['cal'].get((wl,algo),{})
    for model,col,lab in [('default',BLUE,'Default'),('v1',ORANGE,'Calibrated v1 (partial)'),('v2',GREEN,'Calibrated v2 (full)')]:
        pts=d.get(model)
        if not pts: continue
        ax.plot([p[0] for p in pts],[p[1] for p in pts],'o-',color=col,label=lab,lw=1.8,ms=4)
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_title(f'{"JOB" if wl=="JOB" else "TPC-H (SF10)"} — {algo}', fontsize=11)
    ax.set_xlabel('Index budget (MB, log)'); ax.set_ylabel('Total workload runtime (s, log)')
    for s in ('top','right'): ax.spines[s].set_visible(False)
    ax.grid(axis='y', color='#d5d5d5', lw=.6, alpha=.7); ax.set_axisbelow(True)
    ax.legend(fontsize=8, frameon=False)
fig.tight_layout(); fig.savefig(f'{OUT}/fig3_calibration.png', dpi=170, facecolor='white'); plt.close(fig)
print('wrote fig3_calibration')

# ---- FIG 4: cardinality injection — correlation + scatter ----
CI='/Users/adithyaudayan/index_selection_evaluation/cardinality_injection/data/results/measurements.csv'
rows=list(csv.DictReader(open(CI)))
d={(r['query'],r['units'],r['cards']):(float(r['est_cost']),float(r['actual_ms'])) for r in rows}
qs=sorted({r['query'] for r in rows})
qs29=[q for q in qs if q!='27c']
fig, axes = plt.subplots(1,3, figsize=(13.5,4.2))
conds=[('default','default','Default cost model',BLUE),
       ('calibrated','default','Calibrated',ORANGE),
       ('calibrated','injected','Calibrated + injected',GREEN)]
for ax,(u,c,title,col) in zip(axes,conds):
    cs=[d[(q,u,c)][0] for q in qs29]; rs=[d[(q,u,c)][1] for q in qs29]
    rho=st.spearmanr(cs,rs)[0]
    ax.scatter(rs,cs,s=44,color=col,edgecolors='white',lw=.8)
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_title(f'{title}\nSpearman ρ = {rho:.3f}', fontsize=10)
    ax.set_xlabel('Actual runtime (ms, log)')
    for s in ('top','right'): ax.spines[s].set_visible(False)
    ax.grid(color='#d5d5d5', lw=.6, alpha=.6); ax.set_axisbelow(True)
axes[0].set_ylabel('Predicted cost (planner units, log)')
fig.tight_layout(); fig.savefig(f'{OUT}/fig4_injection.png', dpi=170, facecolor='white'); plt.close(fig)
print('wrote fig4_injection')

# ---- FIG 5: interleaved A/B ----
ab=[('2c',39.4,0.00006),('17e',27.3,0.00006),('10a',7.2,0.00006),('32a',4.6,0.018),
    ('8b',1.9,0.00006),('4b',0.8,0.010),('3c',0.6,0.008),('5a',0.6,0.277),
    ('6e',0.1,0.762),('1b',-1.1,0.489),('18b',-1.2,0.0012)]
fig, ax = plt.subplots(figsize=(9,4.4))
cols=[GREEN if (p<0.05 and v>0) else (RED if (p<0.05 and v<0) else GREY) for _,v,p in ab]
ax.bar([a[0] for a in ab],[a[1] for a in ab],color=cols)
ax.axhline(0,color='#444',lw=1)
ax.set_ylabel('Runtime reduction from injection (%)'); ax.set_xlabel('Query')
import matplotlib.patches as mp
ax.legend(handles=[mp.Patch(color=GREEN,label='Significantly faster (p<0.05)'),
                   mp.Patch(color=RED,label='Significantly slower'),
                   mp.Patch(color=GREY,label='No significant change')],fontsize=8,frameon=False)
finish(fig,ax,'fig5_ab',legend=False)

print('\nALL FIGURES:', sorted(os.listdir(OUT)))
