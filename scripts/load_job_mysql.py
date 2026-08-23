"""
load_job_mysql.py  — bulk-loads all JOB CSVs into the VIDEX MySQL container.
Run from the root of index_selection_evaluation/:
    python3 scripts/load_job_mysql.py
"""
import mysql.connector
import os
import time

VIDEX_HOST = "127.0.0.1"
VIDEX_PORT = 13308
VIDEX_USER = "videx"
VIDEX_PASS = "password"
DB = "JOB_REFINED"
DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jobdata")

TABLES = [
    "comp_cast_type", "company_type", "complete_cast", "info_type",
    "kind_type", "link_type", "role_type",          # small lookup tables first
    "keyword", "char_name",
    "company_name", "title", "name",                # medium
    "aka_name", "aka_title", "movie_info_idx",
    "movie_keyword", "movie_link", "complete_cast",
    "movie_companies", "cast_info", "movie_info", "person_info",  # large last
]

# Deduplicate while preserving order
seen = set()
TABLES = [t for t in TABLES if not (t in seen or seen.add(t))]

conn = mysql.connector.connect(
    host=VIDEX_HOST, port=VIDEX_PORT,
    user=VIDEX_USER, password=VIDEX_PASS,
    database=DB,
    allow_local_infile=True,
    autocommit=True,
    connection_timeout=3600,
)
cursor = conn.cursor()
# Enable LOCAL INFILE on the server side
cursor.execute("SET GLOBAL local_infile = 1")

total_start = time.time()

for table in TABLES:
    csv_path = os.path.abspath(os.path.join(DATADIR, f"{table}.csv"))
    if not os.path.exists(csv_path):
        print(f"  [SKIP] {table}.csv not found")
        continue

    # Get column order from information_schema
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA='{DB}' AND TABLE_NAME='{table}' "
        "ORDER BY ORDINAL_POSITION"
    )
    cols = [row[0] for row in cursor.fetchall()]
    col_list = ", ".join(f"`{c}`" for c in cols)

    print(f"  Loading {table}...", end="", flush=True)
    t0 = time.time()
    try:
        cursor.execute(f"TRUNCATE TABLE `{table}`")
        cursor.execute(f"""
            LOAD DATA LOCAL INFILE '{csv_path}'
            INTO TABLE `{table}`
            CHARACTER SET utf8mb4
            FIELDS TERMINATED BY ','
            OPTIONALLY ENCLOSED BY '"'
            LINES TERMINATED BY '\\n'
            ({col_list})
        """)
        elapsed = time.time() - t0
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        count = cursor.fetchone()[0]
        print(f" {count:,} rows  ({elapsed:.1f}s)")
    except Exception as e:
        print(f" ERROR: {e}")

cursor.close()
conn.close()

total = time.time() - total_start
print(f"\nDone! Total time: {total:.0f}s")
print("Verify with: docker exec videx ... -e 'SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema=\"JOB_REFINED\"'")
