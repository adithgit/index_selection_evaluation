import logging
import subprocess
import ibm_db
import ibm_db_dbi
import time
from selection.database_connector import DatabaseConnector


class DB2DatabaseConnector(DatabaseConnector):
    def __init__(self, db_name, autocommit=False):
        DatabaseConnector.__init__(self, db_name, autocommit=autocommit)
        self.db_system = "db2"
        self._connection = None
        self.run_id = None  # shared SYSTOOLS.ADVISE_INSTANCE id for all virtual indexes

        # Default connection properties
        self.host = "localhost"
        self.port = 50000
        self.user = "db2inst1"
        self.password = "secretpassword"
        self.container = "db2server"

        if not self.db_name:
            self.db_name = "imdb"

        self.create_connection()
        logging.debug("DB2 connector created: {}".format(db_name))

    # ------------------------------------------------------------------ #
    #  Connection                                                          #
    # ------------------------------------------------------------------ #

    def create_connection(self):
        if self._connection:
            self.close()
        conn_str = (
            f"DATABASE={self.db_name};"
            f"HOSTNAME={self.host};"
            f"PORT={self.port};"
            f"PROTOCOL=TCPIP;"
            f"UID={self.user};"
            f"PWD={self.password};"
        )
        logging.info("Connecting to DB2 via ibm_db_dbi...")
        try:
            self._connection = ibm_db_dbi.connect(conn_str, "", "")
            self._cursor = self._connection.cursor()
            logging.info("Successfully connected to DB2!")
            # Auto-create required SYSTOOLS tables on every fresh connection
            self._ensure_systools_tables()
        except Exception as e:
            logging.error(f"Failed to connect to DB2: {e}")

    # ------------------------------------------------------------------ #
    #  SYSTOOLS table bootstrap                                            #
    # ------------------------------------------------------------------ #

    def _table_exists(self, schema: str, table: str) -> bool:
        """Return True when SCHEMA.TABLE is present in SYSCAT.TABLES."""
        try:
            res = self.exec_fetch(
                "SELECT COUNT(*) FROM SYSCAT.TABLES "
                f"WHERE TABSCHEMA='{schema}' AND TABNAME='{table}'"
            )
            return bool(res and res[0] > 0)
        except Exception:
            return False

    def _ensure_systools_tables(self):
        """Entry point: create EXPLAIN and ADVISE tables when missing."""
        self._ensure_explain_tables()
        self._ensure_advise_tables()

    def _ensure_explain_tables(self):
        """
        Create the DB2 EXPLAIN tables (EXPLAIN_STATEMENT, EXPLAIN_OBJECT, …)
        in the SYSTOOLS schema using three fallback strategies:

        1. SYSPROC.SYSINSTALLOBJECTS  cleanest, works on most DB2 LUW installs
        2. EXPLAIN.DDL via docker exec when the stored proc is unavailable
        3. Log an error and carry on — cost will fall back to 0.0 gracefully
        """
        if self._table_exists("SYSTOOLS", "EXPLAIN_STATEMENT"):
            logging.info("DB2: EXPLAIN tables already exist — skipping setup")
            return

        logging.info("DB2: EXPLAIN tables missing — attempting to create …")

        # Strategy 1 — built-in stored procedure (preferred)
        try:
            self.exec_only(
                "CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN','C',NULL,'SYSTOOLS')"
            )
            self.commit()
            logging.info("DB2: EXPLAIN tables created via SYSINSTALLOBJECTS")
            return
        except Exception as e:
            logging.warning(
                f"DB2: SYSINSTALLOBJECTS failed ({e}) — trying EXPLAIN.DDL …"
            )

        # Strategy 2 — run EXPLAIN.DDL inside the DB2 Docker container
        ddl_candidates = [
            "~/sqllib/misc/EXPLAIN.DDL",
            "/home/db2inst1/sqllib/misc/EXPLAIN.DDL",
            "$DB2DIR/misc/EXPLAIN.DDL",
        ]
        for ddl_path in ddl_candidates:
            bash_cmd = (
                f"source ~/.bashrc && "
                f"db2 connect to {self.db_name} && "
                f"db2 -tvf {ddl_path} && "
                f"db2 connect reset"
            )
            try:
                result = subprocess.run(
                    ["docker", "exec", "-u", self.user, self.container,
                     "bash", "-lc", bash_cmd],
                    capture_output=True, text=True, timeout=60,
                )
                out = result.stdout + result.stderr
                if result.returncode == 0 or "already exists" in out.lower():
                    logging.info(f"DB2: EXPLAIN tables created from {ddl_path}")
                    return
                logging.debug(f"DDL attempt ({ddl_path}) output: {out[:400]}")
            except Exception as ex:
                logging.debug(f"docker exec failed for {ddl_path}: {ex}")

        logging.error(
            "DB2: Could not create EXPLAIN tables via any strategy. "
            "Query costs will fall back to 0.0."
        )

    def _ensure_advise_tables(self):
        """
        Create SYSTOOLS.ADVISE_INSTANCE and SYSTOOLS.ADVISE_INDEX when missing.

        Tries SYSPROC.SYSINSTALLOBJECTS('DB2ADVIS',…) first; falls back to
        inline DDL that matches the column set used by this connector.
        """
        instance_ok = self._table_exists("SYSTOOLS", "ADVISE_INSTANCE")
        index_ok = self._table_exists("SYSTOOLS", "ADVISE_INDEX")

        if instance_ok and index_ok:
            logging.info("DB2: ADVISE tables already exist — skipping setup")
            return

        logging.info("DB2: ADVISE tables missing — attempting to create …")

        # Strategy 1 — built-in stored procedure
        try:
            self.exec_only(
                "CALL SYSPROC.SYSINSTALLOBJECTS('DB2ADVIS','C',NULL,'SYSTOOLS')"
            )
            self.commit()
            logging.info("DB2: ADVISE tables created via SYSINSTALLOBJECTS")
            return
        except Exception as e:
            logging.warning(
                f"DB2: SYSINSTALLOBJECTS for DB2ADVIS failed ({e}) "
                "— using inline DDL …"
            )

        # Strategy 2 — inline DDL (columns must match _ensure_advise_session
        #               and _simulate_index exactly)
        advise_ddl = {
            "ADVISE_INSTANCE": """
                CREATE TABLE SYSTOOLS.ADVISE_INSTANCE (
                    START_TIME       TIMESTAMP    NOT NULL WITH DEFAULT CURRENT TIMESTAMP,
                    END_TIME         TIMESTAMP,
                    MODE             CHAR(1)      NOT NULL WITH DEFAULT 'I',
                    WKLD_COMPRESSION CHAR(3)      WITH DEFAULT 'MED',
                    STATUS           VARCHAR(10)  WITH DEFAULT 'STARTED'
                )""",
            "ADVISE_INDEX": """
                CREATE TABLE SYSTOOLS.ADVISE_INDEX (
                    NAME             VARCHAR(128) NOT NULL,
                    CREATOR          VARCHAR(128) NOT NULL,
                    TBNAME           VARCHAR(128) NOT NULL,
                    TBCREATOR        VARCHAR(128) NOT NULL,
                    COLNAMES         VARCHAR(640),
                    USE_INDEX        CHAR(1),
                    EXISTS           CHAR(1),
                    CREATION_TEXT    VARCHAR(2000),
                    INDEXTYPE        CHAR(3),
                    COLCOUNT         SMALLINT,
                    NLEAF            INTEGER,
                    NLEVELS          SMALLINT,
                    FULLKEYCARD      BIGINT,
                    FIRSTKEYCARD     BIGINT,
                    CLUSTERRATIO     SMALLINT,
                    UNIQUERULE       CHAR(1),
                    USERDEFINED      SMALLINT,
                    SYSTEM_REQUIRED  SMALLINT,
                    RUN_ID           TIMESTAMP,
                    IID              INTEGER
                )""",
        }

        for table_name, ddl in advise_ddl.items():
            if self._table_exists("SYSTOOLS", table_name):
                logging.info(f"DB2: SYSTOOLS.{table_name} already exists — skipping")
                continue
            try:
                self.exec_only(ddl)
                self.commit()
                logging.info(f"DB2: Created SYSTOOLS.{table_name}")
            except Exception as e:
                logging.error(f"DB2: Failed to create SYSTOOLS.{table_name}: {e}")

    # ------------------------------------------------------------------ #
    #  Standard DatabaseConnector interface                                #
    # ------------------------------------------------------------------ #

    def database_names(self):
        if not self._connection:
            return []
        try:
            stmt = (
                f"SELECT COUNT(*) FROM SYSCAT.TABLES "
                f"WHERE TABSCHEMA = '{self.user.upper()}' AND TYPE='T'"
            )
            res = self.exec_fetch(stmt)
            if res and res[0] > 0:
                return [self.db_name]
            return []
        except Exception:
            return []

    def create_database(self, database_name):
        # Database is pre-created by Docker (DBNAME=imdb)
        pass

    def enable_simulation(self):
        pass

    def create_statistics(self):
        logging.info("DB2: Running RUNSTATS on all user tables via ADMIN_CMD")
        try:
            tables = self.exec_fetch(
                f"SELECT TABNAME FROM SYSCAT.TABLES "
                f"WHERE TABSCHEMA = '{self.user.upper()}'",
                one=False,
            )
            for (table_name,) in (tables or []):
                logging.info(f"DB2: RUNSTATS on {table_name}")
                cmd = (
                    f"RUNSTATS ON TABLE {self.user.upper()}.{table_name} "
                    f"WITH DISTRIBUTION AND DETAILED INDEXES ALL"
                )
                self.exec_only(f"CALL SYSPROC.ADMIN_CMD('{cmd}')")
            self.commit()
            logging.info("DB2: RUNSTATS complete")
        except Exception as e:
            logging.error(f"DB2: RUNSTATS failed: {e}")

    def exec_only(self, statement):
        self._cursor.execute(statement)

    def exec_fetch(self, statement, one=True):
        self._cursor.execute(statement)
        if one:
            return self._cursor.fetchone()
        return self._cursor.fetchall()

    def commit(self):
        if self._connection:
            self._connection.commit()

    def rollback(self):
        if self._connection:
            self._connection.rollback()

    def close(self):
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
        logging.debug("DB2 connector closed")

    def import_data(self, table, path, delimiter=","):
        tmp_csv = f"/tmp/{table}.csv"
        logging.info(f"Copying {path} to {self.container}:{tmp_csv}")
        subprocess.run(["docker", "cp", path, f"{self.container}:{tmp_csv}"])
        logging.info(f"Importing {tmp_csv} into DB2 table {table}")
        stmt = (
            f"CALL SYSPROC.ADMIN_CMD('IMPORT FROM {tmp_csv} OF DEL "
            f"MODIFIED BY COLDEL{delimiter} CHARDEL\"\" DECPT "
            f"MESSAGES ON SERVER INSERT INTO "
            f"{self.user.upper()}.{table.upper()}')"
        )
        try:
            self.exec_only(stmt)
        except Exception as e:
            logging.error(f"Error importing {table}: {e}")
        finally:
            logging.info(f"Cleaning up {tmp_csv} from {self.container}")
            subprocess.run(["docker", "exec", self.container, "rm", tmp_csv])

    def drop_indexes(self):
        logging.info("Dropping indexes in DB2")
        stmt = (
            f"SELECT INDNAME FROM SYSCAT.INDEXES "
            f"WHERE TABSCHEMA = '{self.user.upper()}' "
            f"AND INDSCHEMA = '{self.user.upper()}' "
            f"AND UNIQUERULE = 'D'"
        )
        indexes = self.exec_fetch(stmt, one=False)
        for (index_name,) in (indexes or []):
            drop_stmt = f"DROP INDEX {self.user.upper()}.{index_name}"
            logging.debug(f"Dropping index {index_name}")
            try:
                self.exec_only(drop_stmt)
            except Exception as e:
                logging.error(f"Failed to drop {index_name}: {e}")

    def indexes_size(self):
        """
        Return total size (bytes) of user-created, non-PK indexes in this schema.

        DB2 uses NLEAF = -1 as a sentinel meaning RUNSTATS has never been run
        for that index. Without guarding against it, SUM(-1 * PAGESIZE) goes
        negative and COALESCE collapses the result to 0 — causing "0.0 MB" logs.

        Two guards are applied:
          1. AND I.NLEAF > 0      — excludes -1 sentinel rows at the scan level
          2. NULLIF(I.NLEAF, -1)  — belt-and-suspenders in the aggregate itself

        SYSCAT.INDEXES filters:
          INDSCHEMA = our schema   — only our indexes, not system ones
          UNIQUERULE = 'D'         — plain user indexes only
                                     ('P' = primary key, 'U' = unique constraint)
          SYSTEM_REQUIRED = 0      — skip constraint-backing indexes
          USERDEFINED = 1          — skip auto-generated catalog indexes
          NLEAF > 0                — skip unanalysed indexes (RUNSTATS pending)

        If ALL indexes have NLEAF = -1 (RUNSTATS not yet run at all), the
        primary query returns -1 (sentinel) and a catalog-based fallback fires,
        estimating size from CARD + AVGCOLLEN so the caller never silently
        receives 0.0.
        """
        schema = self.user.upper()

        # ── Primary: use real NLEAF where RUNSTATS has been run ──────────
        stmt = (
            f"SELECT COALESCE(SUM(NULLIF(I.NLEAF, -1) * TS.PAGESIZE), -1) "
            f"FROM SYSCAT.INDEXES I "
            f"JOIN SYSCAT.TABLES T "
            f"  ON T.TABSCHEMA = I.TABSCHEMA AND T.TABNAME = I.TABNAME "
            f"JOIN SYSCAT.TABLESPACES TS "
            f"  ON TS.TBSPACEID = T.TBSPACEID "
            f"WHERE I.INDSCHEMA      = '{schema}' "
            f"  AND I.UNIQUERULE     = 'D' "   # 'P'=primary key, 'U'=unique — both excluded
            f"  AND I.SYSTEM_REQUIRED = 0 "    # skip constraint-backing indexes
            f"  AND I.USERDEFINED    = 1 "     # skip auto-generated catalog indexes
            f"  AND I.NLEAF          > 0 "     # exclude NLEAF=-1 sentinels at row level
        )
        try:
            result = self.exec_fetch(stmt)
            size = float(result[0]) if result and result[0] is not None else -1.0

            if size > 0:
                return size

            # ── Fallback: RUNSTATS not yet run — estimate from CARD + AVGCOLLEN
            logging.warning(
                "DB2: indexes_size — NLEAF not populated (RUNSTATS pending); "
                "falling back to catalog-based estimation."
            )
            fallback_stmt = (
                f"SELECT COALESCE(SUM("
                f"  CEIL(T.CARD * 1.0 / GREATEST(1, "
                f"    FLOOR((TS.PAGESIZE - 64) * 1.0 / "
                f"          GREATEST(1, (SELECT COALESCE(SUM(C.AVGCOLLEN), 40) "
                f"                       FROM SYSCAT.COLUMNS C "
                f"                       WHERE C.TABSCHEMA = I.TABSCHEMA "
                f"                         AND C.TABNAME   = I.TABNAME) + 16)"
                f"    )"
                f"  )) * TS.PAGESIZE"
                f"), 0) "
                f"FROM SYSCAT.INDEXES I "
                f"JOIN SYSCAT.TABLES T "
                f"  ON T.TABSCHEMA = I.TABSCHEMA AND T.TABNAME = I.TABNAME "
                f"JOIN SYSCAT.TABLESPACES TS "
                f"  ON TS.TBSPACEID = T.TBSPACEID "
                f"WHERE I.INDSCHEMA      = '{schema}' "
                f"  AND I.UNIQUERULE     = 'D' "
                f"  AND I.SYSTEM_REQUIRED = 0 "
                f"  AND T.CARD           > 0 "
            )
            fallback = self.exec_fetch(fallback_stmt)
            return float(fallback[0]) if fallback and fallback[0] else 0.0

        except Exception as e:
            logging.error(f"DB2: indexes_size failed: {e}")
            return 0.0

    def create_index(self, index):
        schema = self.user.upper()
        table_name = index.table().name.upper()
        cols_sql = ", ".join([f'"{c.name.upper()}" ASC' for c in index.columns])
        index_name = index.index_idx().upper()

        creation_text = (
            f'CREATE INDEX "{schema}"."{index_name}" '
            f'ON "{schema}"."{table_name}" ({cols_sql}) ALLOW REVERSE SCANS'
        )

        try:
            logging.debug(f"Creating DB2 index: {creation_text}")
            self.exec_only(creation_text)
            self.commit()

            # Fetch both NLEAF and the tablespace page size in one query so
            # estimated_size reflects the actual page size (4K/8K/16K/32K)
            # rather than a hard-coded 8 KiB assumption.
            res = self.exec_fetch(
                f"SELECT I.NLEAF, TS.PAGESIZE "
                f"FROM SYSCAT.INDEXES I "
                f"JOIN SYSCAT.TABLES T "
                f"  ON T.TABSCHEMA = I.TABSCHEMA AND T.TABNAME = I.TABNAME "
                f"JOIN SYSCAT.TABLESPACES TS "
                f"  ON TS.TBSPACEID = T.TBSPACEID "
                f"WHERE I.INDSCHEMA = '{schema}' AND I.INDNAME = '{index_name}'"
            )
            nleaf     = int(res[0]) if res and res[0] is not None and res[0] > 0 else 1
            page_size = int(res[1]) if res and res[1] is not None and res[1] > 0 else 8_192
            index.estimated_size = nleaf * page_size
        except Exception as e:
            logging.warning(f"Failed to create DB2 index {index_name}, ignoring. Error: {e}")
            index.estimated_size = 0

    def drop_index(self, index):
        schema = self.user.upper()
        index_name = index.index_idx().upper()
        statement = f'DROP INDEX "{schema}"."{index_name}"'
        try:
            self.exec_only(statement)
            self.commit()
        except Exception as e:
            logging.debug(f"Failed to drop DB2 index {index_name} (may not exist): {e}")
            self.rollback()

    # ------------------------------------------------------------------ #
    #  Virtual index simulation via SYSTOOLS.ADVISE_INDEX                 #
    # ------------------------------------------------------------------ #

    def _ensure_advise_session(self):
        if self.run_id is not None:
            return
        try:
            self.exec_only(
                "INSERT INTO SYSTOOLS.ADVISE_INSTANCE "
                "(START_TIME, END_TIME, MODE, WKLD_COMPRESSION, STATUS) "
                "VALUES (CURRENT TIMESTAMP, CURRENT TIMESTAMP, 'I', 'MED', 'STARTED')"
            )
            self.commit()
            res = self.exec_fetch(
                "SELECT MAX(START_TIME) FROM SYSTOOLS.ADVISE_INSTANCE"
            )
            self.run_id = res[0]
        except Exception as e:
            logging.error(f"DB2: failed to create ADVISE_INSTANCE row: {e}")
            self.run_id = "2000-01-01-00.00.00.000000"

    # ------------------------------------------------------------------ #
    #  Index size estimation using DB2 catalog statistics                 #
    # ------------------------------------------------------------------ #

    def _estimate_index_size_from_catalog(self, schema: str, table_name: str, cols) -> tuple[int, int]:
        """
        Return (nleaf, estimated_bytes) using DB2 catalog statistics.

        Sources:
          SYSCAT.TABLES      → CARD (row count)
          SYSCAT.COLUMNS     → AVGCOLLEN (avg byte-length per indexed column)
          SYSCAT.TABLESPACES → PAGESIZE (via join through SYSCAT.TABLES)

        B-tree leaf page formula:
          entry_size       = Σ AVGCOLLEN + RID (6 B) + per-entry overhead (10 B)
          entries_per_page = (PAGESIZE - page_header) ÷ entry_size
          nleaf            = ⌈ CARD ÷ entries_per_page ⌉
          estimated_bytes  = nleaf × PAGESIZE

        Falls back gracefully to conservative defaults when RUNSTATS has not
        been run yet (CARD = -1 or AVGCOLLEN = -1 in the catalog).
        """
        PAGE_HEADER    = 64   # bytes reserved per B-tree leaf page
        RID_SIZE       = 6    # Row-ID appended to every index entry
        ENTRY_OVERHEAD = 10   # per-entry slot / pointer overhead

        # 1. Table cardinality and tablespace page size
        try:
            row = self.exec_fetch(
                f"SELECT T.CARD, TS.PAGESIZE "
                f"FROM SYSCAT.TABLES T "
                f"JOIN SYSCAT.TABLESPACES TS ON TS.TBSPACEID = T.TBSPACEID "
                f"WHERE T.TABSCHEMA = '{schema}' AND T.TABNAME = '{table_name}'"
            )
            card      = int(row[0]) if row and row[0] and row[0] > 0 else 50_000
            page_size = int(row[1]) if row and row[1] and row[1] > 0 else 8_192
        except Exception as e:
            logging.warning(f"DB2: could not read table stats for {table_name}: {e}")
            card, page_size = 50_000, 8_192

        # 2. Sum of average column lengths for every indexed column
        col_list = ", ".join(f"'{c.name.upper()}'" for c in cols)
        try:
            row = self.exec_fetch(
                f"SELECT SUM(AVGCOLLEN) "
                f"FROM SYSCAT.COLUMNS "
                f"WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table_name}' "
                f"AND COLNAME IN ({col_list})"
            )
            avg_key_len = int(row[0]) if row and row[0] and row[0] > 0 else 40
        except Exception as e:
            logging.warning(f"DB2: could not read column stats for {table_name}: {e}")
            avg_key_len = 40

        # 3. B-tree leaf-page formula
        entry_size       = avg_key_len + RID_SIZE + ENTRY_OVERHEAD
        usable_per_page  = max(1, page_size - PAGE_HEADER)
        entries_per_page = max(1, usable_per_page // entry_size)
        nleaf            = max(1, -(-card // entries_per_page))  # ceiling division
        estimated_bytes  = nleaf * page_size

        logging.debug(
            f"DB2 index size estimate for {table_name}({col_list}): "
            f"card={card}, page_size={page_size}, avg_key_len={avg_key_len}, "
            f"entry_size={entry_size}, entries_per_page={entries_per_page}, "
            f"nleaf={nleaf}, estimated_bytes={estimated_bytes}"
        )
        return nleaf, estimated_bytes

    def _simulate_index(self, index):
        logging.info(f"Simulating index in DB2: {index.joined_column_names()}")
        schema     = self.user.upper()
        table_name = index.table().name.upper()
        cols       = index.columns
        col_count  = len(cols)
        index_name = (
            f"V_{table_name}_{col_count}_"
            f"{abs(hash(index.joined_column_names())) % 100_000}"
        )

        # DB2 COLNAMES format: +COL1-COL2-…
        col_names = "+" + cols[0].name.upper()
        for col in cols[1:]:
            col_names += f"-{col.name.upper()}"

        # Catalog-based size estimation (replaces the old colcard // 43 heuristic)
        nleaf, estimated_bytes = self._estimate_index_size_from_catalog(
            schema, table_name, cols
        )
        nlevels = 2 if nleaf <= 50 else (3 if nleaf <= 5_000 else 4)

        # FULLKEYCARD / FIRSTKEYCARD from actual column cardinality
        try:
            row = self.exec_fetch(
                f"SELECT MIN(COLCARD) "
                f"FROM SYSCAT.COLUMNS "
                f"WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table_name}' "
                f"AND COLNAME IN ({', '.join(chr(39)+c.name.upper()+chr(39) for c in cols)})"
            )
            colcard = int(row[0]) if row and row[0] and row[0] > 0 else 10_000
        except Exception:
            colcard = 10_000

        cols_sql      = ", ".join([f'"{c.name.upper()}" ASC' for c in cols])
        creation_text = (
            f'CREATE INDEX "{schema}"."{index_name}" '
            f'ON "{schema}"."{table_name}" ({cols_sql}) ALLOW REVERSE SCANS'
        )

        self._ensure_advise_session()
        iid = abs(hash(index_name)) % 32_700

        stmt = (
            "INSERT INTO SYSTOOLS.ADVISE_INDEX "
            "(NAME, CREATOR, TBNAME, TBCREATOR, COLNAMES, USE_INDEX, EXISTS, "
            " CREATION_TEXT, INDEXTYPE, COLCOUNT, NLEAF, NLEVELS, FULLKEYCARD, "
            " FIRSTKEYCARD, CLUSTERRATIO, UNIQUERULE, USERDEFINED, "
            " SYSTEM_REQUIRED, RUN_ID, IID) "
            f"VALUES ('{index_name}', '{schema}', '{table_name}', '{schema}', "
            f"'{col_names}', 'Y', 'N', '{creation_text}', 'REG', "
            f"{col_count}, {nleaf}, {nlevels}, {colcard}, {colcard}, "
            f"80, 'D', 1, 0, '{self.run_id}', {iid})"
        )
        try:
            self.exec_only(stmt)
            self.commit()
        except Exception as e:
            logging.error(f"Failed to simulate virtual index {index_name}: {e}")

        index.estimated_size = estimated_bytes
        return (index_name, index_name)

    def _drop_simulated_index(self, identifier):
        try:
            self.exec_only(
                f"DELETE FROM SYSTOOLS.ADVISE_INDEX WHERE NAME = '{identifier}'"
            )
            self.commit()
        except Exception as e:
            logging.error(f"Failed to drop simulated virtual index {identifier}: {e}")

    # ------------------------------------------------------------------ #
    #  Query cost / plan                                                   #
    # ------------------------------------------------------------------ #


    def exec_query(self, query, timeout=None, cost_evaluation=False):
        if not cost_evaluation:
            self.commit()

        query_text = query.text.strip().rstrip(";")
        plan = self._get_plan(query)

        timeout_s = int(timeout / 1000) if timeout is not None else None

        _timed_out   = [False]
        _watchdog    = None

        if timeout_s is not None:
            # IMPORTANT: We must fetch the application handle on the MAIN thread.
            # If we try to fetch it inside the watchdog while the main thread is
            # blocking on exec_fetch, the watchdog will also block forever!
            app_handle = None
            try:
                res = self.exec_fetch(
                    "SELECT AGENT_ID FROM SYSIBMADM.APPLICATIONS "
                    "WHERE AGENT_ID = MON_GET_APPLICATION_HANDLE()"
                )
                if res and res[0]:
                    app_handle = res[0]
            except Exception:
                pass

            if app_handle is not None:
                def _force_application():
                    """Fired by threading.Timer after timeout_s seconds."""
                    _timed_out[0] = True
                    logging.warning(
                        f"DB2: query {query.nr} exceeded {timeout_s}s — "
                        f"forcing application handle {app_handle}"
                    )
                    try:
                        # Open a dedicated kill-connection
                        conn_str = (
                            f"DATABASE={self.db_name};"
                            f"HOSTNAME={self.host};"
                            f"PORT={self.port};"
                            f"PROTOCOL=TCPIP;"
                            f"UID={self.user};"
                            f"PWD={self.password};"
                        )
                        import ibm_db_dbi
                        kill_conn = ibm_db_dbi.connect(conn_str, "", "")
                        kill_cur  = kill_conn.cursor()
                        kill_cur.execute(
                            f"CALL SYSPROC.ADMIN_CMD('FORCE APPLICATION ({app_handle})')"
                        )
                        kill_conn.commit()
                        kill_cur.close()
                        kill_conn.close()
                    except Exception as e:
                        logging.error(f"DB2: watchdog FORCE APPLICATION failed: {e}")

                import threading
                _watchdog = threading.Timer(timeout_s, _force_application)
                _watchdog.daemon = True
                _watchdog.start()

        start_time = time.time()
        try:
            self.exec_fetch(query_text, one=False)
            exec_time_ms = (time.time() - start_time) * 1000
            result = exec_time_ms, plan
        except Exception as e:
            elapsed_s = time.time() - start_time
            if _timed_out[0]:
                logging.warning(
                    f"DB2: query {query.nr} was killed after {elapsed_s:.1f}s "
                    f"(limit={timeout_s}s)"
                )
            else:
                logging.error(f"Error executing query {query.nr}: {e}")
            
            try:
                self._connection.rollback()
            except Exception:
                pass
            
            # CRITICAL: We must rebuild the connection! FORCE APPLICATION severes
            # the connection permanently. Without this, all 113 remaining queries will fail.
            if _timed_out[0] or "Connection" in str(e) or "CLI" in str(e):
                logging.info(f"DB2: Reconnecting to DB2...")
                self.create_connection()
                
            result = None, plan
        finally:
            if _watchdog is not None:
                _watchdog.cancel()   # disarm if query finished in time

        return result

    def _get_cost(self, query):
        return self._get_plan(query)["Total Cost"]

    def _get_plan(self, query):
        query_text = query.text.strip().rstrip(";")
        try:
            # Clear old explain data — objects first, statement second
            # (reversed order fixes a bug where the EXPLAIN_OBJECT DELETE
            #  used a subquery against already-deleted EXPLAIN_STATEMENT rows)
            self.exec_only("DELETE FROM SYSTOOLS.EXPLAIN_OBJECT")
            self.exec_only("DELETE FROM SYSTOOLS.EXPLAIN_STATEMENT")
        except Exception:
            pass

        try:
            self.exec_only("SET CURRENT EXPLAIN MODE = EVALUATE INDEXES")
            self.exec_only(query_text)
            self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            self.commit()

            cost_res = self.exec_fetch(
                "SELECT TOTAL_COST FROM SYSTOOLS.EXPLAIN_STATEMENT "
                "ORDER BY EXPLAIN_TIME DESC FETCH FIRST 1 ROW ONLY"
            )
            cost = (
                float(cost_res[0])
                if cost_res and cost_res[0] is not None
                else 0.0
            )

            plan_objects = self.exec_fetch(
                "SELECT DISTINCT O.OBJECT_NAME "
                "FROM SYSTOOLS.EXPLAIN_OBJECT O "
                "WHERE O.EXPLAIN_TIME = "
                "  (SELECT MAX(EXPLAIN_TIME) FROM SYSTOOLS.EXPLAIN_STATEMENT) "
                "AND O.EXPLAIN_LEVEL = 'P'",
                one=False,
            )
            plan_str = (
                " ".join(row[0] for row in plan_objects) if plan_objects else ""
            )
            return {"Total Cost": cost, "Plan": plan_str}

        except Exception as e:
            try:
                db2_err = ibm_db.stmt_errormsg()
                logging.error(
                    f"Failed to get plan for query {query.text}: {e}. "
                    f"DB2 Error: {db2_err}"
                )
            except Exception:
                logging.error(f"Failed to get plan for query {query.text}: {e}")
            return {"Total Cost": 0.0, "Plan": ""}
        finally:
            try:
                self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Misc                                                                #
    # ------------------------------------------------------------------ #

    def estimate_index_size(self, index_oid):
        """
        Estimate the size of an index by its OID using catalog statistics.

        If index_oid is a non-numeric string (a simulated index name like
        'V_TITLE_2_…'), we resolve it against SYSTOOLS.ADVISE_INDEX to fetch
        the estimated NLEAF that was written at simulation time.

        Otherwise we query the actual physical index in SYSCAT.INDEXES and
        multiply by the tablespace page size so the result is correct across
        4K / 8K / 16K / 32K tablespace configurations.
        """
        schema = self.user.upper()

        if isinstance(index_oid, str) and not index_oid.isdigit():
            # Virtual / simulated index — look up in ADVISE_INDEX
            try:
                res = self.exec_fetch(
                    f"SELECT NLEAF FROM SYSTOOLS.ADVISE_INDEX "
                    f"WHERE CREATOR = '{schema}' AND NAME = '{index_oid}'"
                )
                nleaf = int(res[0]) if res and res[0] is not None and res[0] > 0 else 1
                return nleaf * 8_192   # simulated indexes always use the default page size
            except Exception as e:
                logging.warning(
                    f"DB2: estimate_index_size could not resolve virtual index "
                    f"{index_oid}: {e}. Returning single-page default."
                )
                return 8_192

        # Physical index — resolve IID → NLEAF + PAGESIZE
        try:
            res = self.exec_fetch(
                f"SELECT I.NLEAF, TS.PAGESIZE "
                f"FROM SYSCAT.INDEXES I "
                f"JOIN SYSCAT.TABLES T "
                f"  ON T.TABSCHEMA = I.TABSCHEMA AND T.TABNAME = I.TABNAME "
                f"JOIN SYSCAT.TABLESPACES TS "
                f"  ON TS.TBSPACEID = T.TBSPACEID "
                f"WHERE I.INDSCHEMA = '{schema}' "
                f"  AND I.IID = {int(index_oid)}"
            )
            nleaf     = int(res[0]) if res and res[0] is not None and res[0] > 0 else 1
            page_size = int(res[1]) if res and res[1] is not None and res[1] > 0 else 8_192
            return nleaf * page_size
        except Exception as e:
            logging.warning(
                f"DB2: estimate_index_size could not resolve physical OID "
                f"{index_oid}: {e}. Returning single-page default."
            )
            return 8_192

    def all_simulated_indexes(self):
        try:
            res = self.exec_fetch(
                "SELECT NAME FROM SYSTOOLS.ADVISE_INDEX", one=False
            )
            return [[row[0], row[0]] for row in res] if res else []
        except Exception:
            return []