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
        3. Log an error and carry on cost will fall back to 0.0 gracefully
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

    def close(self):
        if self._connection:
            self._cursor.close()
            self._connection.close()
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
        stmt = (
            f"SELECT SUM(INDEX_OBJECT_P_SIZE) * 1024 "
            f"FROM SYSIBMADM.ADMINTABINFO "
            f"WHERE TABSCHEMA = '{self.user.upper()}'"
        )
        try:
            result = self.exec_fetch(stmt)
            return float(result[0]) if result and result[0] else 0.0
        except Exception:
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

            res = self.exec_fetch(
                f"SELECT NLEAF FROM SYSCAT.INDEXES "
                f"WHERE INDSCHEMA = '{schema}' AND INDNAME = '{index_name}'"
            )
            nleaf = res[0] if res and res[0] is not None and res[0] > 0 else 1
            index.estimated_size = nleaf * 8 * 1024
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

    def _simulate_index(self, index):
        logging.info(f"Simulating index in DB2: {index.joined_column_names()}")
        schema = self.user.upper()
        table_name = index.table().name.upper()
        cols = index.columns
        col_count = len(cols)
        index_name = (
            f"V_{table_name}_{col_count}_"
            f"{abs(hash(index.joined_column_names())) % 100000}"
        )

        # DB2 COLNAMES format: +COL1-COL2-…
        col_names = "+" + cols[0].name.upper()
        for col in cols[1:]:
            col_names += f"-{col.name.upper()}"

        try:
            col_stats = self.exec_fetch(
                f"SELECT MIN(COLCARD) FROM SYSCAT.COLUMNS "
                f"WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table_name}' "
                f"AND COLNAME IN "
                f"({', '.join(chr(39) + c.name.upper() + chr(39) for c in cols)})"
            )
            colcard = (
                int(col_stats[0])
                if col_stats and col_stats[0] is not None and col_stats[0] > 0
                else 10_000
            )
        except Exception as e:
            logging.error(f"Error fetching column stats for {table_name}: {e}")
            colcard = 10_000

        nleaf = max(1, colcard // 43)
        nlevels = 3 if col_count <= 2 else 4
        cols_sql = ", ".join([f'"{c.name.upper()}" ASC' for c in cols])
        creation_text = (
            f'CREATE INDEX "{schema}"."{index_name}" '
            f'ON "{schema}"."{table_name}" ({cols_sql}) ALLOW REVERSE SCANS'
        )

        self._ensure_advise_session()
        run_id = self.run_id
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
            f"80, 'D', 1, 0, '{run_id}', {iid})"
        )
        try:
            self.exec_only(stmt)
            self.commit()
        except Exception as e:
            logging.error(f"Failed to simulate virtual index {index_name}: {e}")

        index.estimated_size = nleaf * 8 * 1024
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
        
        # Get the plan to fulfill the benchmark's expectations
        plan = self._get_plan(query)
        
        start_time = time.time()
        try:
            self.exec_fetch(query_text, one=False)
            exec_time_ms = (time.time() - start_time) * 1000
            result = exec_time_ms, plan
        except Exception as e:
            logging.error(f"Error executing query {query.nr}: {e}")
            self._connection.rollback()
            result = None, plan
            
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
        return 8192 * 1000

    def all_simulated_indexes(self):
        try:
            res = self.exec_fetch(
                "SELECT NAME FROM SYSTOOLS.ADVISE_INDEX", one=False
            )
            return [[row[0], row[0]] for row in res] if res else []
        except Exception:
            return []