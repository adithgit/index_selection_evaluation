import csv, json, os, statistics, math
from collections import defaultdict
BR='/Users/adithyaudayan/index_selection_evaluation/benchmark_results'
csv.field_size_limit(10**9)

def load(fn):
    """returns list of dicts: {budget, per-query {q: (runtime_ms, cost)}, meta}"""
    path=os.path.join(BR,fn)
    if not os.path.exists(path): return None
    rows=[]
    with open(path) as f:
        rd=csv.reader(f,delimiter=';')
        hdr=next(rd)
        qcols=[(i,h) for i,h in enumerate(hdr) if h.endswith('.sql') or (h.startswith('q') and h[1:].strip().isdigit())]
        for r in rd:
            if len(r)<len(hdr)-1: continue
            try: params=json.loads(r[3])
            except Exception: params={}
            budget=params.get('budget_MB')
            per={}
            for i,h in qcols:
                try:
                    d=json.loads(r[i])
                    rt=d.get('Runtimes') or [None]
                    rt=rt[0] if rt else None
                    per[h[:-4] if h.endswith('.sql') else h]=(rt, d.get('Cost'))
                except Exception: pass
            rows.append(dict(budget=budget, per=per,
                             algo=r[2], db=r[6],
                             n_indexes=r[11] if len(r)>11 else None))
    return rows

def total_runtime(row):
    v=[rt for rt,_ in row['per'].values() if rt is not None]
    return sum(v) if v else None
def geomean(xs):
    xs=[x for x in xs if x and x>0]
    return math.exp(sum(math.log(x) for x in xs)/len(xs)) if xs else None

FILES={
 ('JOB','postgres','extend'):'results_extend_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','postgres','db2advis'):'results_db2advis_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','postgres','relaxation'):'results_relaxation_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','postgres','no_index'):'results_no_index_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','db2','extend'):'results_extend_JOB_REFINED_db2_33_queries.csv',
 ('JOB','db2','db2advis'):'results_db2advis_JOB_REFINED_db2_33_queries.csv',
 ('JOB','db2','no_index'):'results_no_index_JOB_REFINED_db2_33_queries.csv',
 ('JOB','mysql','extend'):'results_extend_JOB_REFINED_mysql_33_queries.csv',
 ('JOB','mysql','db2advis'):'results_db2advis_JOB_REFINED_mysql_33_queries.csv',
 ('JOB','mysql','no_index'):'results_no_index_JOB_REFINED_mysql_33_queries.csv',
}
CAL={
 ('JOB','relaxation','default'):'results_relaxation_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','relaxation','v1'):'results_relaxation_JOB_REFINED_postgres_33_queries_calibrated.csv',
 ('JOB','relaxation','v2'):'results_relaxation_JOB_REFINED_postgres_33_queries_calibrated_v2.csv',
 ('JOB','db2advis','default'):'results_db2advis_JOB_REFINED_postgres_33_queries.csv',
 ('JOB','db2advis','v1'):'results_db2advis_JOB_REFINED_postgres_33_queries_calibrated.csv',
 ('JOB','db2advis','v2'):'results_db2advis_JOB_REFINED_postgres_33_queries_calibrated_v2.csv',
 ('TPCH','relaxation','default'):'results_relaxation_tpch_postgres_19_queries.csv',
 ('TPCH','relaxation','v1'):'results_relaxation_tpch_postgres_19_queries_calibrated.csv',
 ('TPCH','relaxation','v2'):'results_relaxation_tpch_postgres_19_queries_calibrated_v2.csv',
 ('TPCH','db2advis','default'):'results_db2advis_tpch_postgres_19_queries.csv',
 ('TPCH','db2advis','v1'):'results_db2advis_tpch_postgres_19_queries_calibrated.csv',
 ('TPCH','db2advis','v2'):'results_db2advis_tpch_postgres_19_queries_calibrated_v2.csv',
}

out={}
print('='*70); print('BASELINES (no_index)')
base={}
for db in ['postgres','db2','mysql']:
    r=load(FILES[('JOB',db,'no_index')])
    if not r: print(f'  {db}: MISSING'); continue
    tot=[total_runtime(x) for x in r if total_runtime(x)]
    med=statistics.median(tot) if tot else None
    base[db]=med
    per=r[0]['per']
    print(f'  {db:9s} runs={len(r)}  median total={med/1000:.1f}s  queries_with_runtime={sum(1 for v in per.values() if v[0])}')
out['baseline']=base

print('='*70); print('SPEEDUP BY BUDGET (JOB, total runtime vs no_index median)')
speed=defaultdict(dict)
for db in ['postgres','db2','mysql']:
    for algo in ['extend','db2advis','relaxation']:
        k=('JOB',db,algo)
        if k not in FILES: continue
        r=load(FILES[k])
        if not r: continue
        b=base.get(db)
        pts=[]
        for x in r:
            t=total_runtime(x)
            if t and x['budget'] and b: pts.append((x['budget'], b/t))
        if pts:
            pts.sort()
            speed[db][algo]=pts
            sp=[s for _,s in pts]
            print(f'  {db:9s} {algo:11s} budgets={len(pts):2d}  speedup min={min(sp):.2f} med={statistics.median(sp):.2f} max={max(sp):.2f}')
out['speedup']=speed

print('='*70); print('COST vs RUNTIME correlation (per DBMS, JOB, all budgets pooled)')
from scipy import stats as st
corr={}
for db in ['postgres','db2','mysql']:
    cs,rs=[],[]
    for algo in ['extend','db2advis','relaxation']:
        k=('JOB',db,algo)
        if k not in FILES: continue
        r=load(FILES[k]) or []
        for x in r:
            for q,(rt,c) in x['per'].items():
                if rt and c: cs.append(c); rs.append(rt)
    if len(cs)>10:
        rho=st.spearmanr(cs,rs)[0]
        corr[db]=(rho,len(cs))
        print(f'  {db:9s} n={len(cs):5d}  Spearman(cost,runtime) = {rho:+.3f}')
out['corr']=corr

print('='*70); print('CALIBRATION: total workload runtime by budget (seconds)')
cal=defaultdict(dict)
for (wl,algo,model),fn in CAL.items():
    r=load(fn)
    if not r: print(f'  {wl} {algo} {model}: MISSING'); continue
    pts=sorted([(x['budget'], total_runtime(x)/1000) for x in r if x['budget'] and total_runtime(x)])
    cal[(wl,algo)][model]=pts
    print(f'  {wl:5s} {algo:11s} {model:8s} budgets={len(pts):2d} total={sum(v for _,v in pts):9.1f}s')
out['cal']=cal

import pickle
pickle.dump(out, open('/tmp/all_results.pkl','wb'))
print('\nsaved /tmp/all_results.pkl')
