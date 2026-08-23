"""
test_mysql_connector.py
Full integration test for MySQLDatabaseConnector:
  1. Virtual index CREATE via VIDEX
  2. Cost estimation via EXPLAIN FORMAT=JSON on shadow db
  3. Physical index CREATE on real InnoDB db
  4. All cleanup (drop virtual + physical indexes)

Run from project root:
    source .venv/bin/activate
    python3 scripts/test_mysql_connector.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from selection.dbms.mysql_dbms import MySQLDatabaseConnector
from selection.workload import Column, Table, Index

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def ok(msg): print(f"  {PASS} {msg}")
def fail(msg): print(f"  {FAIL} {msg}"); sys.exit(1)
def info(msg): print(f"  {INFO} {msg}")

# ─────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────
section("Connecting to MySQL + VIDEX")
try:
    db = MySQLDatabaseConnector("JOB_REFINED")
    ok("Connected to real InnoDB db (JOB_REFINED)")
    ok(f"Connected to VIDEX shadow db ({db.videx_db_name})")
except Exception as e:
    fail(f"Connection failed: {e}")

# Build a test index object: title(kind_id)
table = Table("title")
col = Column("kind_id")
col.table = table
table.add_column(col)
index = Index([col])  # Index takes a list of columns directly
info(f"Test index: {index.index_idx()} on title(kind_id)")

# ─────────────────────────────────────────────────────────────
# Test 1: Virtual Index Creation
# ─────────────────────────────────────────────────────────────
section("Test 1: Virtual Index Creation (VIDEX)")
try:
    result = db._simulate_index(index)
    index_oid, index_name_ret = result   # (oid_equivalent, name) tuple
    ok(f"_simulate_index() returned (oid, name): ('{index_oid}', '{index_name_ret}')")

    # Verify it actually exists in the VIDEX shadow db
    db._videx_cursor.execute(
        f"SELECT INDEX_NAME FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA='{db.videx_db_name}' AND INDEX_NAME='{index_oid}' LIMIT 1"
    )
    row = db._videx_cursor.fetchone()
    if row:
        ok(f"Confirmed: index '{row[0]}' exists in {db.videx_db_name}")
    else:
        fail("Index NOT found in VIDEX shadow db after simulate_index()")
except Exception as e:
    fail(f"_simulate_index() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 2: Cost Estimation (EXPLAIN via VIDEX)
# ─────────────────────────────────────────────────────────────
section("Test 2: Cost Estimation via EXPLAIN FORMAT=JSON")

# Build a minimal Query-like object
class FakeQuery:
    nr = "test_q1"
    text = "SELECT COUNT(*) FROM title WHERE kind_id = 1"
    columns = [col]

    def text_split(self): return [self.text]

try:
    q = FakeQuery()
    cost = db._get_cost(q)
    if cost and cost > 0:
        ok(f"_get_cost() returned cost = {cost:.2f}  ✓ (non-zero means VIDEX stats server is responding)")
    else:
        fail(f"_get_cost() returned {cost} — VIDEX stats server may not be responding")
except Exception as e:
    fail(f"_get_cost() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 2b: estimate_index_size (the budget enforcement fix)
# ─────────────────────────────────────────────────────────────
section("Test 2b: estimate_index_size (budget enforcement)")
try:
    size_bytes = db.estimate_index_size(index_oid)
    size_mb = size_bytes / 1024 / 1024
    if size_bytes > 0:
        ok(f"estimate_index_size('{index_oid}') = {size_bytes:,} bytes ({size_mb:.1f} MB)  ✓ non-zero")
    else:
        fail(f"estimate_index_size returned 0 — budget constraints will NOT work!")
except Exception as e:
    fail(f"estimate_index_size() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 3: Cleanup — Drop Virtual Index
# ─────────────────────────────────────────────────────────────
section("Test 3: Cleanup — Drop Virtual Index")
try:
    db._drop_simulated_index(index_oid)
    ok(f"_drop_simulated_index('{index_oid}') called successfully")

    # Verify it's gone
    db._videx_cursor.execute(
        f"SELECT COUNT(*) FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA='{db.videx_db_name}' AND INDEX_NAME='{index_oid}'"
    )
    count = db._videx_cursor.fetchone()[0]
    if count == 0:
        ok("Confirmed: virtual index removed from VIDEX shadow db")
    else:
        fail("Virtual index still present after drop!")
except Exception as e:
    fail(f"_drop_simulated_index() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 4: Physical Index Creation on Real InnoDB
# ─────────────────────────────────────────────────────────────
section("Test 4: Physical Index Creation (Real InnoDB)")
try:
    # Reset estimated size
    index.estimated_size = None
    db.create_index(index)
    ok(f"create_index() completed. estimated_size = {index.estimated_size:,} bytes")

    # Verify real index exists on InnoDB
    db._cursor.execute(
        f"SELECT INDEX_NAME FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA='JOB_REFINED' AND TABLE_NAME='title' "
        f"AND INDEX_NAME='{index.index_idx()}' LIMIT 1"
    )
    row = db._cursor.fetchone()
    if row:
        ok(f"Confirmed: physical index '{row[0]}' exists in InnoDB JOB_REFINED.title")
    else:
        fail("Physical index NOT found in InnoDB after create_index()")
except Exception as e:
    fail(f"create_index() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 5: Actual Query Execution on Real DB
# ─────────────────────────────────────────────────────────────
section("Test 5: Actual Query Execution (Real InnoDB)")
try:
    runtime_ms, _ = db.exec_query(q)
    if runtime_ms and runtime_ms > 0:
        ok(f"exec_query() ran in {runtime_ms:.1f} ms")
    else:
        fail(f"exec_query() returned unexpected runtime: {runtime_ms}")
except Exception as e:
    fail(f"exec_query() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 6: Full cleanup — drop_indexes()
# ─────────────────────────────────────────────────────────────
section("Test 6: Cleanup — drop_indexes() (all real indexes)")
try:
    before = db.number_of_indexes()
    info(f"Indexes before drop_indexes(): {before}")
    db.drop_indexes()
    after = db.number_of_indexes()
    info(f"Indexes after drop_indexes():  {after}")
    if after == 0:
        ok("All real indexes dropped successfully")
    else:
        fail(f"{after} indexes still remain after drop_indexes()")
except Exception as e:
    fail(f"drop_indexes() raised: {e}")

# ─────────────────────────────────────────────────────────────
# Test 7: Index Size Estimation
# ─────────────────────────────────────────────────────────────
section("Test 7: indexes_size()")
try:
    size = db.indexes_size()
    ok(f"indexes_size() = {size:,} bytes  (0 is correct — all indexes just dropped)")
except Exception as e:
    fail(f"indexes_size() raised: {e}")

# ─────────────────────────────────────────────────────────────
section("ALL TESTS PASSED ✓")
db.close()
