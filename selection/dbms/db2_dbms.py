import logging
import ibm_db
import ibm_db_dbi

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
        
        if not self.db_name:
            self.db_name = "imdb"
            
        self.create_connection()
        logging.debug("DB2 connector created: {}".format(db_name))

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
        
        logging.info(f"Connecting to DB2 via ibm_db_dbi...")
        try:
            self._connection = ibm_db_dbi.connect(conn_str, "", "")
            self._cursor = self._connection.cursor()
            logging.info("Successfully connected to DB2!")
        except Exception as e:
            logging.error(f"Failed to connect to DB2: {e}")

    def database_names(self):
        # In DB2, we can just return [self.db_name] if it connects
        # Or query databases. Since we already connected to a specific DB in create_connection,
        # we can just return it. 
        # Actually, IBM DB2 doesn't have "CREATE DATABASE" from SQL easily, it's typically an instance-level command.
        # So we'll just mock it and return [self.db_name] to prevent the framework from trying to create it if it exists.
        return [self.db_name]

    def create_database(self, database_name):
        # We assume the database is already created by docker (DBNAME=imdb)
        pass

    def enable_simulation(self):
        pass

    def create_statistics(self):
        logging.info("DB2: Running RUNSTATS on all user tables via ADMIN_CMD")
        try:
            tables = self.exec_fetch(
                f"SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = '{self.user.upper()}'",
                one=False
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
        import subprocess
        import os
        
        container_name = "db2server"
        tmp_csv = f"/tmp/{table}.csv"
        
        logging.info(f"Copying {path} to {container_name}:{tmp_csv}")
        subprocess.run(["docker", "cp", path, f"{container_name}:{tmp_csv}"])
        
        logging.info(f"Importing {tmp_csv} into DB2 table {table}")
        # Use DB2 ADMIN_CMD to run IMPORT
        stmt = f"CALL SYSPROC.ADMIN_CMD('IMPORT FROM {tmp_csv} OF DEL MODIFIED BY COLDEL{delimiter} CHARDEL\"\" DECPT MESSAGES ON SERVER INSERT INTO {self.user.upper()}.{table.upper()}')"
        
        try:
            self.exec_only(stmt)
        except Exception as e:
            logging.error(f"Error importing {table}: {e}")
        finally:
            logging.info(f"Cleaning up {tmp_csv} from {container_name}")
            subprocess.run(["docker", "exec", container_name, "rm", tmp_csv])

    def drop_indexes(self):
        logging.info("Dropping indexes in DB2")
        stmt = f"SELECT INDNAME FROM SYSCAT.INDEXES WHERE TABSCHEMA = '{self.user.upper()}' AND INDSCHEMA = '{self.user.upper()}' AND UNIQUERULE = 'D'"
        indexes = self.exec_fetch(stmt, one=False)
        for index in indexes:
            index_name = index[0]
            drop_stmt = f"DROP INDEX {self.user.upper()}.{index_name}"
            logging.debug(f"Dropping index {index_name}")
            try:
                self.exec_only(drop_stmt)
            except Exception as e:
                logging.error(f"Failed to drop {index_name}: {e}")

    def indexes_size(self):
        # Returns physical index size in bytes for the schema
        stmt = f"SELECT SUM(INDEX_OBJECT_P_SIZE) * 1024 FROM SYSIBMADM.ADMINTABINFO WHERE TABSCHEMA = '{self.user.upper()}'"
        try:
            result = self.exec_fetch(stmt)
            return float(result[0]) if result and result[0] else 0.0
        except Exception:
            return 0.0


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
            res = self.exec_fetch("SELECT MAX(START_TIME) FROM SYSTOOLS.ADVISE_INSTANCE")
            self.run_id = res[0]
        except Exception as e:
            logging.error(f"DB2: failed to create ADVISE_INSTANCE: {e}")
            self.run_id = "2000-01-01-00.00.00.000000"

    def _simulate_index(self, index):
        logging.info(f"Simulating index in DB2: {index.joined_column_names()}")

        table_name = index.table().name.upper()
        schema = self.user.upper()
        cols = index.columns
        col_count = len(cols)

        index_name = f"V_{table_name}_{col_count}_{abs(hash(index.joined_column_names())) % 100000}"

        # DB2 COLNAMES format: +COL1-COL2-... (hyphen-separated after the first column)
        col_names = "+" + cols[0].name.upper()
        for col in cols[1:]:
            col_names += f"-{col.name.upper()}"

        try:
            col_stats = self.exec_fetch(
                f"SELECT MIN(COLCARD) FROM SYSCAT.COLUMNS "
                f"WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table_name}' "
                f"AND COLNAME IN ({', '.join(chr(39) + c.name.upper() + chr(39) for c in cols)})"
            )
            colcard = int(col_stats[0]) if col_stats and col_stats[0] is not None and col_stats[0] > 0 else 10000
        except Exception as e:
            logging.error(f"Error fetching column stats for {table_name}: {e}")
            colcard = 10000

        # Leaf page estimate aligned with db2advis (~40 rows per leaf for IMDB-scale data)
        nleaf = max(1, colcard // 43)
        nlevels = 3 if col_count <= 2 else 4

        cols_sql = ", ".join([f'"{c.name.upper()}" ASC' for c in cols])
        creation_text = (
            f'CREATE INDEX "{schema}"."{index_name}" ON "{schema}"."{table_name}" '
            f"({cols_sql}) ALLOW REVERSE SCANS"
        )

        self._ensure_advise_session()
        run_id = self.run_id

        iid = abs(hash(index_name)) % 32700

        stmt = f"""
        INSERT INTO SYSTOOLS.ADVISE_INDEX
        (NAME, CREATOR, TBNAME, TBCREATOR, COLNAMES, USE_INDEX, EXISTS, CREATION_TEXT, INDEXTYPE,
         COLCOUNT, NLEAF, NLEVELS, FULLKEYCARD, FIRSTKEYCARD, CLUSTERRATIO, UNIQUERULE,
         USERDEFINED, SYSTEM_REQUIRED, RUN_ID, IID)
        VALUES ('{index_name}', '{schema}', '{table_name}', '{schema}', '{col_names}', 'Y', 'N',
                '{creation_text}', 'REG',
                {col_count}, {nleaf}, {nlevels}, {colcard}, {colcard}, 80, 'D', 1, 0, '{run_id}', {iid})
        """
        
        try:
            self.exec_only(stmt)
            self.commit()
        except Exception as e:
            logging.error(f"Failed to simulate virtual index {index_name}: {e}")
        
        # Store estimated size (bytes) on the index object for budget tracking
        index.estimated_size = nleaf * 8 * 1024
        
        return (index_name, index_name)

    def _drop_simulated_index(self, identifier):
        stmt = f"DELETE FROM SYSTOOLS.ADVISE_INDEX WHERE NAME = '{identifier}'"
        try:
            self.exec_only(stmt)
            self.commit()
        except Exception as e:
            logging.error(f"Failed to drop simulated virtual index {identifier}: {e}")

    def _get_cost(self, query):
        plan = self._get_plan(query)
        return plan["Total Cost"]

    def _get_plan(self, query):
        query_text = query.text.strip()
        if query_text.endswith(";"):
            query_text = query_text[:-1]

        try:
            self.exec_only("DELETE FROM SYSTOOLS.EXPLAIN_STATEMENT")
            self.exec_only(
                "DELETE FROM SYSTOOLS.EXPLAIN_OBJECT "
                "WHERE EXPLAIN_TIME IN (SELECT EXPLAIN_TIME FROM SYSTOOLS.EXPLAIN_STATEMENT)"
            )
        except Exception:
            pass

        try:
            # On DB2 LUW, virtual indexes from ADVISE_INDEX are only considered when the
            # statement is compiled in EVALUATE INDEXES mode via normal execution.
            # EXPLAIN PLAN FOR does not pick them up.
            self.exec_only("SET CURRENT EXPLAIN MODE = EVALUATE INDEXES")
            self.exec_only(query_text)
            self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            self.commit()

            cost_res = self.exec_fetch(
                "SELECT TOTAL_COST FROM SYSTOOLS.EXPLAIN_STATEMENT "
                "ORDER BY EXPLAIN_TIME DESC FETCH FIRST 1 ROW ONLY"
            )
            cost = float(cost_res[0]) if cost_res and cost_res[0] is not None else 0.0

            plan_objects = self.exec_fetch(
                "SELECT DISTINCT O.OBJECT_NAME "
                "FROM SYSTOOLS.EXPLAIN_OBJECT O "
                "WHERE O.EXPLAIN_TIME = (SELECT MAX(EXPLAIN_TIME) FROM SYSTOOLS.EXPLAIN_STATEMENT) "
                "AND O.EXPLAIN_LEVEL = 'P'",
                one=False,
            )
            plan_str = " ".join([row[0] for row in plan_objects]) if plan_objects else ""

            return {"Total Cost": cost, "Plan": plan_str}
        except Exception as e:
            try:
                import ibm_db
                db2_err = ibm_db.stmt_errormsg()
                logging.error(
                    f"Failed to get plan for query {query_id}: {e}. DB2 Error: {db2_err}"
                )
            except Exception:
                logging.error(f"Failed to get plan for query {query_id}: {e}")
            return {"Total Cost": 0.0, "Plan": ""}
        finally:
            try:
                self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            except Exception:
                pass

    def estimate_index_size(self, index_oid):
        return 8192 * 1000

    def all_simulated_indexes(self):
        try:
            stmt = "SELECT NAME FROM SYSTOOLS.ADVISE_INDEX"
            res = self.exec_fetch(stmt, one=False)
            return [[row[0], row[0]] for row in res] if res else []
        except Exception:
            return []
