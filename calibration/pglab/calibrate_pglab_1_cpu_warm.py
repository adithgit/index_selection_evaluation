"""Wu et al. calibration for the pg_lab PG18 instance (port 5433) -- PHASE 1.
Identical protocol to calibrate_1_cpu_warm.py, retargeted at the pg_lab build:
warm CPU probes on a table fitting shared_buffers (128MB on this instance),
solving cpu_tuple / cpu_index_tuple / cpu_operator; also builds the big cold
tables for PHASE 2.
"""
import psycopg2, json, statistics, os
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 5433
N_CPU=2_000_000
N_IO=6_000_000; PAD=150
REPS=7

def ex(conn,sql):
    with conn.cursor() as c: c.execute(sql)
def one(conn,sql):
    with conn.cursor() as c: c.execute(sql); return c.fetchone()
def ea(conn,sql):
    with conn.cursor() as c:
        c.execute("explain (analyze,buffers,timing on,format json) "+sql); p=c.fetchone()[0][0]
    def blk(n):
        b=n.get("Shared Hit Blocks",0)+n.get("Shared Read Blocks",0)
        for ch in n.get("Plans",[]): b+=blk(ch)
        return b
    return p["Execution Time"], blk(p["Plan"])
def measure(conn,sql,force_index=False):
    ex(conn,"set max_parallel_workers_per_gather=0"); ex(conn,"set jit=off")
    ex(conn,f"set enable_seqscan={'off' if force_index else 'on'}")
    ex(conn,f"set enable_bitmapscan={'off' if force_index else 'on'}")
    t=[]; b=[]
    for _ in range(REPS):
        et,bl=ea(conn,sql); t.append(et); b.append(bl)
    return statistics.median(t), statistics.median(b)

admin=psycopg2.connect(dbname="postgres",port=PORT); admin.autocommit=True
ex(admin,"drop database if exists pgcalib"); ex(admin,"create database pgcalib"); admin.close()
conn=psycopg2.connect(dbname="pgcalib",port=PORT); conn.autocommit=True
ex(conn,"set max_parallel_workers_per_gather=0")
print("building r_cpu + r_seq/r_rand on pg_lab (port 5433)...",flush=True)
ex(conn,f"create table r_cpu as select g id,(random()*{N_CPU})::int a from generate_series(1,{N_CPU}) g")
ex(conn,"create index r_cpu_id on r_cpu(id)")
ex(conn,f"create table r_seq as select g id,(random()*{N_IO})::int a, repeat('x',{PAD})::char({PAD}) pad from generate_series(1,{N_IO}) g")
ex(conn,f"create table r_rand as select g id,(random()*{N_IO})::int a, repeat('x',{PAD})::char({PAD}) pad from generate_series(1,{N_IO}) g")
ex(conn,"create index r_rand_a on r_rand(a)")
for t in ("r_cpu","r_seq","r_rand"): ex(conn,f"analyze {t}")
def stats(t):
    r=one(conn,f"select relpages,reltuples::bigint from pg_class where relname='{t}'"); return r[0],r[1]
Pc,Tc=stats("r_cpu"); Ps,Ts=stats("r_seq"); Pr,Tr=stats("r_rand")
print(f"r_cpu {Pc}pg/{Tc}tup | r_seq {Ps}pg/{Ts}tup | r_rand {Pr}pg/{Tr}tup",flush=True)

for _ in range(4): ex(conn,"select count(*) from r_cpu"); ex(conn,"select * from r_cpu offset 1e9")

import numpy as np
obs=[]; tv=[]
def add(sql,n,fi=False):
    et,bl=measure(conn,sql,fi); obs.append(n); tv.append(et)
    print(f"  {sql[:44]:44s} {et:8.3f}ms blk={bl}",flush=True)
print("warm CPU probes:",flush=True)
add("select * from r_cpu", [Tc,0,0])
add("select count(*) from r_cpu", [Tc,0,Tc])
add("select * from r_cpu where id<200000", [0,0,0], True)
for k in (200000,1000000):
    m=one(conn,f"select count(*) from r_cpu where id<{k}")[0]
    add(f"select * from r_cpu where id<{k}", [m,m,m], True)
for ops,expr in [(1,"a<2147483647"),(4,"(a*3+7)<2147483647"),(8,"(a*3+a*5+a*7+11)<2147483647")]:
    add(f"select count(*) from r_cpu where {expr}", [Tc,0,(ops+1)*Tc])
obs.pop(2); tv.pop(2)
from scipy.optimize import nnls
c,_=nnls(np.array(obs,float),np.array(tv,float))
ct,ci,co=c
print(f"\ncpu_tuple_cost(abs ms)={ct:.6g}  cpu_index_tuple_cost={ci:.6g}  cpu_operator_cost={co:.6g}")
json.dump({"ct":ct,"ci":ci,"co":co,"r_seq":{"P":int(Ps),"T":int(Ts)},
           "r_rand":{"P":int(Pr),"T":int(Tr)}}, open(os.path.join(HERE,"_partial_pglab.json"),"w"),indent=2)
conn.close()
print("\nsaved calibration/_partial_pglab.json. NEXT: pg_ctl restart + sudo purge, then calibrate_pglab_2_io_cold.py")
