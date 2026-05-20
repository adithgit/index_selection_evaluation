import logging
import ibm_db
import ibm_db_dbi

from selection.database_connector import DatabaseConnector


class DB2DatabaseConnector(DatabaseConnector):
    def __init__(self, db_name, autocommit=False):
        DatabaseConnector.__init__(self, db_name, autocommit=autocommit)
        self.db_system = "db2"
        self._connection = None
        
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
        # We can implement RUNSTATS here later if needed, but for now we skip to allow the framework to proceed
        logging.info("DB2: Skipping RUNSTATS for now")
        pass

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

    def _simulate_index(self, index):
        logging.info(f"Simulating index in DB2: {index.joined_column_names()}")
        
        table_name = index.table().name.upper()
        cols = index.columns
        
        # Create a unique name for the virtual index
        index_name = f"V_{table_name}_{len(index.columns)}_{abs(hash(index.joined_column_names())) % 100000}"
        
        # In DB2, virtual indexes are defined by inserting into SYSTOOLS.ADVISE_INDEX
        col_names = "+".join([c.name.upper() for c in cols]) + "+"
        
        stmt = f"""
        INSERT INTO SYSTOOLS.ADVISE_INDEX 
        (NAME, TBNAME, TBCREATOR, COLNAMES, USE_INDEX, EXISTS)
        VALUES ('{index_name}', '{table_name}', '{self.user.upper()}', '{col_names}', 'Y', 'N')
        """
        try:
            self.exec_only(stmt)
        except Exception as e:
            logging.error(f"Failed to simulate index {index_name}: {e}")
        
        # Return name as both OID and name
        return (index_name, index_name)

    def _drop_simulated_index(self, identifier):
        stmt = f"DELETE FROM SYSTOOLS.ADVISE_INDEX WHERE NAME = '{identifier}'"
        try:
            self.exec_only(stmt)
        except Exception as e:
            logging.error(f"Failed to drop simulated index {identifier}: {e}")

    def _get_cost(self, query):
        plan = self._get_plan(query)
        return plan["Total Cost"]

    def _get_plan(self, query):
        query_id = abs(hash(query.text)) % 2147483647
        
        query_text = query.text.strip()
        if query_text.endswith(";"):
            query_text = query_text[:-1]

        try:
            self.exec_only("SET CURRENT EXPLAIN MODE = EVALUATE INDEXES")
            self.exec_only(f"EXPLAIN PLAN SET QUERYNO = {query_id} FOR {query_text}")
            self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            
            cost_res = self.exec_fetch(f"SELECT TOTAL_COST FROM SYSTOOLS.EXPLAIN_STATEMENT WHERE QUERYNO = {query_id}")
            cost = float(cost_res[0]) if cost_res else 0.0
            plan_str = ""
            
        except Exception as e:
            logging.error(f"Failed to get plan for query {query_id}: {e}")
            cost = 0.0
            plan_str = ""
        finally:
            try:
                self.exec_only("SET CURRENT EXPLAIN MODE = NO")
            except:
                pass
                
        return {"Total Cost": cost, "Plan": plan_str}

    def estimate_index_size(self, index_oid):
        return 8192 * 1000

    def all_simulated_indexes(self):
        try:
            stmt = "SELECT NAME FROM SYSTOOLS.ADVISE_INDEX"
            res = self.exec_fetch(stmt, one=False)
            return [[row[0], row[0]] for row in res] if res else []
        except Exception:
            return []
