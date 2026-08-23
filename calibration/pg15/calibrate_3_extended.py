"""Extended calibration: the cost parameters Wu et al. (and our phase 1/2) left out.
  A. parallel_setup_cost / parallel_tuple_cost  (warm, isolation probes)
  B. procost for string functions vs int compare (warm micro-benchmarks)
  C. JIT compile cost -> rational jit_above_cost  (measured from EXPLAIN ANALYZE)
  D. effective_io_concurrency sweep (COLD bitmap scans, purge between settings)
  E. effective_cache_size (sized from RAM, not measured)
Anchored to phase-1/2 absolute units: c_s=0.0011704 ms, c_t=4.134e-5, c_o=2.329e-6.
"""
import psycopg2, subprocess, time, json, statistics, sys

C_S=0.0011704120232418802; C_T=4.134360902255638e-05; C_O=2.328879699248122e-06
DB="pgcalib2"; OUT={}

def sh(cmd): return subprocess.run(cmd, capture_output=True, text=True)
def restart_and_purge():
    sh(["brew","services","restart","postgresql@15"])
    for _ in range(60):
        if sh(["/opt/homebrew/bin/pg_isready","-q"]).returncode==0: break
        time.sleep(1)
    r=sh(["sudo","-n","/usr/sbin/purge"])
    assert r.returncode==0, "purge failed"

def con(db=DB):
    c=psycopg2.connect(dbname=db); c.autocommit=True; return c

def ea_ms(cur, sql, reps=5, jit=False):
    """median EXPLAIN ANALYZE execution time (instrumentation-consistent)."""
    ts=[]
    for _ in range(reps):
        cur.execute(("set jit=on;" if jit else "set jit=off;"))
        cur.execute("explain (analyze, timing off, format json) "+sql)
        ts.append(cur.fetchone()[0][0]["Execution Time"])
    return statistics.median(ts)

# ---------- setup ----------
a=con("postgres"); ac=a.cursor()
ac.execute(f"drop database if exists {DB}"); ac.execute(f"create database {DB}"); a.close()
c=con(); cur=c.cursor()
print("building tables...", flush=True)
cur.execute("create table t_small as select g i from generate_series(1,10000) g")
cur.execute("create table t_big   as select g i from generate_series(1,4000000) g")
cur.execute("""create table strs as select g i,
  md5(g::text)||md5((g*7)::text) txt from generate_series(1,2000000) g""")  # 64-char strings
cur.execute("""create table t_io as select g id,(random()*6000000)::int r,
  repeat('x',120)::char(120) pad from generate_series(1,6000000) g""")
cur.execute("create index t_io_r on t_io(r)")
for t in ("t_small","t_big","strs","t_io"): cur.execute(f"analyze {t}")

def warm(t):
    cur.execute(f"select count(*) from {t}"); cur.execute(f"select count(*) from {t}")

SER="set max_parallel_workers_per_gather=0"
PAR=("set max_parallel_workers_per_gather=2; set parallel_setup_cost=0;"
     "set parallel_tuple_cost=0; set min_parallel_table_scan_size=0;"
     "set min_parallel_index_scan_size=0")

# ---------- A. parallel units ----------
print("\nA. parallel units", flush=True)
warm("t_small"); warm("t_big")
cur.execute(SER); ts_small=ea_ms(cur,"select count(*) from t_small",9)
cur.execute(PAR); tp_small=ea_ms(cur,"select count(*) from t_small",9)
setup_ms=max(tp_small-ts_small,0.0)
# tuple transfer: parallel SELECT * slope between 1M and 4M rows through Gather
cur.execute("create table t_1m as select g i from generate_series(1,1000000) g")
cur.execute("analyze t_1m"); warm("t_1m")
cur.execute(PAR)
tp_sel_1m=ea_ms(cur,"select * from t_1m",5)
tp_sel_4m=ea_ms(cur,"select * from t_big",5)
slope=(tp_sel_4m-tp_sel_1m)/3_000_000.0        # ms per tuple through Gather
ptuple_ms=max(slope - C_T/3.0, 0.0)            # remove the (shared) scan component
OUT["parallel_setup_cost"]={"ms":setup_ms,"units":round(setup_ms/C_S,1),"pg_default":1000}
OUT["parallel_tuple_cost"]={"ms":ptuple_ms,"units":round(ptuple_ms/C_S,5),"pg_default":0.1}
print(f"  setup: {setup_ms:.2f} ms  -> {setup_ms/C_S:,.0f} units (default 1000)")
print(f"  tuple: {ptuple_ms*1e6:.1f} ns -> {ptuple_ms/C_S:.4f} units (default 0.1)")

# ---------- B. procost for string functions ----------
print("\nB. procost (string ops vs int compare)", flush=True)
warm("strs"); cur.execute(SER); cur.execute("set jit=off")
base   =ea_ms(cur,"select count(*) from strs",7)
t_int  =ea_ms(cur,"select count(*) from strs where i = -1",7)
t_texteq=ea_ms(cur,"select count(*) from strs where txt = 'zzz'",7)
t_like =ea_ms(cur,"select count(*) from strs where txt like '%zzz%'",7)
t_regex=ea_ms(cur,"select count(*) from strs where txt ~ 'zzz'",7)
N=2_000_000
res={}
for name,t in [("texteq",t_texteq),("textlike",t_like),("textregexeq",t_regex)]:
    d=max(t-base,0)/N                       # ms per row
    pc=d/C_O                                # in units of measured cpu_operator_cost
    res[name]={"ns_per_row":round(d*1e6,1),"procost_measured":round(pc,1),"procost_catalog":1}
    print(f"  {name:12s}: {d*1e6:8.1f} ns/row  -> procost {pc:6.1f}  (catalog says 1)")
print(f"  (denominator: phase-1 cpu_operator_cost = {C_O*1e6:.2f} ns; 64-char strings)")
OUT["procost"]=res

# ---------- C. JIT compile cost ----------
print("\nC. JIT compile cost", flush=True)
cur.execute(SER)
cur.execute("select pg_jit_available()")
if not cur.fetchone()[0]:
    print("  pg_jit_available()=false on this build; thresholds moot"); OUT["jit"]={"available":False}
else:
 cur.execute("set jit=on; set jit_above_cost=0; set jit_inline_above_cost=0; set jit_optimize_above_cost=0")
cur.execute("explain (analyze, timing off, format json) "
            "select sum(i*2+1), avg(i), count(*) from t_big where i%3=0")
plan=cur.fetchone()[0][0]
jit=plan.get("JIT",{}).get("Timing",{})
jit_ms=jit.get("Total", None)
if jit_ms:
    OUT["jit"]={"compile_ms":jit_ms,"breakeven_units":round(jit_ms/C_S,0),
                "pg_default_jit_above_cost":100000}
    print(f"  compile+emit: {jit_ms:.1f} ms -> break-even at ~{jit_ms/C_S:,.0f} cost units "
          f"(default threshold 100,000)")
else:
    print("  JIT timing not reported; skipped"); OUT["jit"]=None
c.close()

# ---------- D. effective_io_concurrency sweep (COLD) ----------
print("\nD. effective_io_concurrency (cold bitmap scans)", flush=True)
sweep={}
for eic in [0,1,16,64,200]:
    restart_and_purge()
    cc=con(); k=cc.cursor()
    try:
        k.execute(f"set effective_io_concurrency={eic}")
    except psycopg2.errors.InvalidParameterValue:
        print(f"  eic={eic}: platform lacks posix_fadvise -> knob locked at 0"); cc.close(); break
    k.execute("set enable_seqscan=off; set enable_indexscan=off")  # force bitmap
    k.execute("set max_parallel_workers_per_gather=0; set jit=off")
    t0=time.perf_counter()
    k.execute("select count(pad) from t_io where r between 100000 and 160000")
    k.fetchall()
    ms=(time.perf_counter()-t0)*1000
    sweep[eic]=round(ms,0); cc.close()
    print(f"  eic={eic:>3}: {ms:8.0f} ms", flush=True)
best=min(sweep,key=sweep.get)
OUT["effective_io_concurrency"]={"sweep_ms":sweep,"recommended":best,"pg_default":0}

# ---------- E. effective_cache_size ----------
ram=int(subprocess.run(["sysctl","-n","hw.memsize"],capture_output=True,text=True).stdout)//2**30
rec=int(ram*0.75)
OUT["effective_cache_size"]={"recommended_gb":rec,"pg_default_gb":4,
    "note":"capacity is knowable (~75% of RAM); actual cache STATE is not a constant"}
print(f"\nE. effective_cache_size: recommend {rec}GB (RAM {ram}GB; default 4GB)")

json.dump(OUT,open("/private/tmp/claude-501/-Users-adithyaudayan-index-selection-evaluation/592f79a0-8fbd-4de7-a331-548a2e9409a7/scratchpad/calib_extended.json","w"),indent=2)
print("\nsaved calib_extended.json")
a=con("postgres"); a.cursor().execute(f"drop database if exists {DB}"); a.close()
print("dropped", DB)
