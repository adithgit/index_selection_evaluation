"""Extended calibration for pg_lab PG18 (port 5433): parallel units, procost,
JIT compile cost, effective_cache_size. Skips the effective_io_concurrency
sweep (macOS lacks posix_fadvise; knob locked at 0 -- prior finding).
Anchors to the phase-1/2 pg_lab absolute units read from
postgres_cost_units_pglab.json. Emits calibration/calib_extended_pglab.json.
"""
import psycopg2, subprocess, json, statistics, os
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 5433
base=json.load(open(os.path.join(HERE,"postgres_cost_units_pglab.json")))["absolute_ms_per_op"]
C_S=base["seq_page_cost"]; C_T=base["cpu_tuple_cost"]; C_O=base["cpu_operator_cost"]
DB="pgcalib2"; OUT={}

def con(db=DB):
    c=psycopg2.connect(dbname=db,port=PORT); c.autocommit=True; return c

def ea_ms(cur, sql, reps=5):
    ts=[]
    for _ in range(reps):
        cur.execute("set jit=off")
        cur.execute("explain (analyze, timing off, format json) "+sql)
        ts.append(cur.fetchone()[0][0]["Execution Time"])
    return statistics.median(ts)

a=con("postgres"); ac=a.cursor()
ac.execute(f"drop database if exists {DB}"); ac.execute(f"create database {DB}"); a.close()
c=con(); cur=c.cursor()
print("building tables...", flush=True)
cur.execute("create table t_small as select g i from generate_series(1,10000) g")
cur.execute("create table t_big   as select g i from generate_series(1,4000000) g")
cur.execute("""create table strs as select g i,
  md5(g::text)||md5((g*7)::text) txt from generate_series(1,2000000) g""")
cur.execute("create table t_1m as select g i from generate_series(1,1000000) g")
for t in ("t_small","t_big","strs","t_1m"): cur.execute(f"analyze {t}")

def warm(t):
    cur.execute(f"select count(*) from {t}"); cur.execute(f"select count(*) from {t}")

SER="set max_parallel_workers_per_gather=0"
PAR=("set max_parallel_workers_per_gather=2; set parallel_setup_cost=0;"
     "set parallel_tuple_cost=0; set min_parallel_table_scan_size=0;"
     "set min_parallel_index_scan_size=0")

print("\nA. parallel units", flush=True)
warm("t_small"); warm("t_big"); warm("t_1m")
cur.execute(SER); ts_small=ea_ms(cur,"select count(*) from t_small",9)
cur.execute(PAR); tp_small=ea_ms(cur,"select count(*) from t_small",9)
setup_ms=max(tp_small-ts_small,0.0)
cur.execute(PAR)
tp_sel_1m=ea_ms(cur,"select * from t_1m",5)
tp_sel_4m=ea_ms(cur,"select * from t_big",5)
slope=(tp_sel_4m-tp_sel_1m)/3_000_000.0
ptuple_ms=max(slope - C_T/3.0, 0.0)
OUT["parallel_setup_cost"]={"ms":setup_ms,"units":round(setup_ms/C_S,1),"pg_default":1000}
OUT["parallel_tuple_cost"]={"ms":ptuple_ms,"units":round(ptuple_ms/C_S,5),"pg_default":0.1}
print(f"  setup: {setup_ms:.2f} ms -> {setup_ms/C_S:,.0f} units (default 1000)")
print(f"  tuple: {ptuple_ms*1e6:.1f} ns -> {ptuple_ms/C_S:.4f} units (default 0.1)")

print("\nB. procost (string ops vs int compare)", flush=True)
warm("strs"); cur.execute(SER)
base_t =ea_ms(cur,"select count(*) from strs",7)
t_texteq=ea_ms(cur,"select count(*) from strs where txt = 'zzz'",7)
t_like =ea_ms(cur,"select count(*) from strs where txt like '%zzz%'",7)
t_regex=ea_ms(cur,"select count(*) from strs where txt ~ 'zzz'",7)
N=2_000_000
res={}
for name,t in [("texteq",t_texteq),("textlike",t_like),("textregexeq",t_regex)]:
    d=max(t-base_t,0)/N
    pc=d/C_O
    res[name]={"ns_per_row":round(d*1e6,1),"procost_measured":round(pc,1),"procost_catalog":1}
    print(f"  {name:12s}: {d*1e6:8.1f} ns/row -> procost {pc:6.1f} (catalog 1)")
OUT["procost"]=res

print("\nC. JIT compile cost", flush=True)
cur.execute(SER)
cur.execute("select pg_jit_available()")
if not cur.fetchone()[0]:
    print("  JIT not available in this build"); OUT["jit"]={"available":False}
else:
    cur.execute("set jit=on; set jit_above_cost=0; set jit_inline_above_cost=0; set jit_optimize_above_cost=0")
    cur.execute("explain (analyze, timing off, format json) "
                "select sum(i*2+1), avg(i), count(*) from t_big where i%3=0")
    plan=cur.fetchone()[0][0]
    jit=plan.get("JIT",{}).get("Timing",{})
    jit_ms=jit.get("Total", None)
    if jit_ms:
        OUT["jit"]={"available":True,"compile_ms":jit_ms,"breakeven_units":round(jit_ms/C_S,0),
                    "pg_default_jit_above_cost":100000}
        print(f"  compile+emit: {jit_ms:.1f} ms -> break-even ~{jit_ms/C_S:,.0f} units (default 100k)")
    else:
        print("  JIT timing not reported"); OUT["jit"]={"available":True,"compile_ms":None}
c.close()

ram=int(subprocess.run(["sysctl","-n","hw.memsize"],capture_output=True,text=True).stdout)//2**30
rec=int(ram*0.75)
OUT["effective_cache_size"]={"recommended_gb":rec,"pg_default_gb":4}
print(f"\nE. effective_cache_size: recommend {rec}GB (RAM {ram}GB)")
OUT["effective_io_concurrency"]={"skipped":"macOS lacks posix_fadvise (prior finding); PG18 io_method=worker untested"}

json.dump(OUT,open(os.path.join(HERE,"calib_extended_pglab.json"),"w"),indent=2)
print("\nsaved calibration/calib_extended_pglab.json")
a=con("postgres"); a.cursor().execute(f"drop database if exists {DB}"); a.close()
print("dropped", DB)
