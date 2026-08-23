import json
import logging
import os
import re
import time

import psycopg2

from selection.database_connector import DatabaseConnector


class PostgresDatabaseConnector(DatabaseConnector):
    def __init__(self, db_name, autocommit=False):
        DatabaseConnector.__init__(self, db_name, autocommit=autocommit)
        self.db_system = "postgres"
        self._connection = None

        if not self.db_name:
            self.db_name = "postgres"
        self.create_connection()

        self.set_random_seed()

        logging.debug("Postgres connector created: {}".format(db_name))

    def create_connection(self):
        if self._connection:
            self.close()
        options = self._cost_guc_options()
        if options:
            self._connection = psycopg2.connect(dbname=self.db_name, options=options)
        else:
            self._connection = psycopg2.connect("dbname={}".format(self.db_name))
        self._connection.autocommit = self.autocommit
        self._cursor = self._connection.cursor()

    @staticmethod
    def _cost_guc_options():
        # Cost-model toggle, applied as libpq startup options so the units persist
        # for the whole session (every what-if EXPLAIN and every timed query),
        # immune to rollback in the cost-estimation loop. See calibration/README.md.
        #   (unset)                 -> stock PostgreSQL defaults
        #   PG_COST_GUCS=calibrated -> load calibration/pg15/postgres_cost_units.json
        #   PG_COST_GUCS="random_page_cost=80;cpu_tuple_cost=0.035"  -> explicit GUCs
        gucs = os.environ.get("PG_COST_GUCS", "").strip()
        if not gucs:
            return None
        if gucs.lower() == "calibrated":
            path = os.path.join(
                os.path.dirname(__file__), "..", "..", "calibration", "pg15",
                "postgres_cost_units.json"
            )
            with open(path) as f:
                units = json.load(f)["gucs"]
            parts = ["{}={}".format(k, v) for k, v in units.items()]
        else:
            parts = [p.strip().replace(" ", "") for p in gucs.split(";") if p.strip()]
        logging.info("Postgres cost calibration active: %s", "; ".join(parts))
        return " ".join("-c {}".format(p) for p in parts)

    def enable_simulation(self):
        self.exec_only("create extension if not exists hypopg")
        self.commit()

    def database_names(self):
        result = self.exec_fetch("select datname from pg_database", False)
        return [x[0] for x in result]

    # Updates query syntax to work in PostgreSQL
    def update_query_text(self, text):
        text = text.replace(";\nlimit ", " limit ").replace("limit -1", "")
        text = re.sub(r" ([0-9]+) days\)", r" interval '\1 days')", text)
        text = self._add_alias_subquery(text)
        return text

    # PostgreSQL requires an alias for subqueries
    def _add_alias_subquery(self, query_text):
        text = query_text.lower()
        positions = []
        for match in re.finditer(r"((from)|,)[  \n]*\(", text):
            counter = 1
            pos = match.span()[1]
            while counter > 0:
                char = text[pos]
                if char == "(":
                    counter += 1
                elif char == ")":
                    counter -= 1
                pos += 1
            next_word = query_text[pos:].lstrip().split(" ")[0].split("\n")[0]
            if next_word[0] in [")", ","] or next_word in [
                "limit",
                "group",
                "order",
                "where",
            ]:
                positions.append(pos)
        for pos in sorted(positions, reverse=True):
            query_text = query_text[:pos] + " as alias123 " + query_text[pos:]
        return query_text

    def create_database(self, database_name):
        self.exec_only("create database {}".format(database_name))
        logging.info("Database {} created".format(database_name))

    def import_data(self, table, path, delimiter="|"):
        with open(path, "r") as file:
            self._cursor.copy_from(file, table, sep=delimiter, null="")

    def indexes_size(self):
        # Returns size in bytes
        statement = (
            "select sum(pg_indexes_size(table_name::text)) from "
            "(select table_name from information_schema.tables "
            "where table_schema='public') as all_tables"
        )
        result = self.exec_fetch(statement)
        return result[0]

    def drop_database(self, database_name):
        statement = f"DROP DATABASE {database_name};"
        self.exec_only(statement)

        logging.info(f"Database {database_name} dropped")

    def create_statistics(self):
        logging.info("Postgres: Run `analyze`")
        self.commit()
        self._connection.autocommit = True
        self.exec_only("analyze")
        self._connection.autocommit = self.autocommit

    def set_random_seed(self, value=0.17):
        logging.info(f"Postgres: Set random seed `SELECT setseed({value})`")
        self.exec_only(f"SELECT setseed({value})")

    def supports_index_simulation(self):
        if self.db_system == "postgres":
            return True
        return False

    def _simulate_index(self, index):
        # Manually enforce B-Tree physical limits by checking column types
        for column in index.columns:
            query = f"SELECT data_type FROM information_schema.columns WHERE table_name = '{column.table.name}' AND column_name = '{column.name}'"
            result = self.exec_fetch(query, one=True)
            if result:
                data_type = result[0]
                if data_type in ['text', 'character varying']:
                    # Return dummy unique OID and name, preventing HypoPG simulation
                    # Use a negative hash to ensure uniqueness and avoid KeyErrors during drop
                    dummy_oid = -abs(hash(index.index_idx()) % 1000000000) - 1
                    return (dummy_oid, "invalid_index_dummy")

        table_name = index.table()
        statement = (
            "select * from hypopg_create_index( "
            f"'create index on {table_name} "
            f"({index.joined_column_names()})')"
        )
        result = self.exec_fetch(statement)
        return result

    def _drop_simulated_index(self, oid):
        if oid <= 0:
            return
        statement = f"select * from hypopg_drop_index({oid})"
        result = self.exec_fetch(statement)

        assert result[0] is True, f"Could not drop simulated index with oid = {oid}."

    def estimate_index_size(self, index_oid):
        if index_oid <= 0:
            return 999999999999  # Make it infinitely large so the budget rejects it
        statement = f"select hypopg_relation_size({index_oid})"
        result = self.exec_fetch(statement)[0]
        assert result > 0, "Hypothetical index does not exist."
        return result

    def all_simulated_indexes(self):
        # HypoPG 1.4.x uses hypopg() instead of hypopg_list_indexes()
        try:
            statement = "select indexrelid, indexname from hypopg()"
            indexes = self.exec_fetch(statement, one=False)
        except Exception:
            statement = "select * from hypopg_list_indexes()"
            indexes = self.exec_fetch(statement, one=False)
        return indexes

    def create_index(self, index):
        table_name = index.table()
        statement = (
            f"create index {index.index_idx()} "
            f"on {table_name} ({index.joined_column_names()})"
        )
        try:
            self.exec_only(statement)
            size = self.exec_fetch(
                f"select relpages from pg_class c " f"where c.relname = '{index.index_idx()}'"
            )
            size = size[0]
            index.estimated_size = size * 8 * 1024
            self.commit()
        except psycopg2.Error as e:
            logging.warning(f"Failed to create index {index.index_idx()}, ignoring. Error: {e}")
            self._connection.rollback()
            index.estimated_size = 0

    def drop_indexes(self):
        logging.info("Dropping indexes")
        stmt = "select indexname from pg_indexes where schemaname='public' and indexname not in (select conname from pg_constraint where contype = 'p')"
        indexes = self.exec_fetch(stmt, one=False)
        for index in indexes:
            index_name = index[0]
            drop_stmt = "drop index {}".format(index_name)
            logging.debug("Dropping index {}".format(index_name))
            self.exec_only(drop_stmt)

    # PostgreSQL expects the timeout in milliseconds
    def exec_query(self, query, timeout=None, cost_evaluation=False):
        # Committing to not lose indexes after timeout
        if not cost_evaluation:
            self._connection.commit()
        query_text = self._prepare_query(query)
        if timeout:
            set_timeout = f"set statement_timeout={timeout}"
            self.exec_only(set_timeout)
        statement = f"explain (analyze, buffers, format json) {query_text}"
        start_time = time.time()
        try:
            plan = self.exec_fetch(statement, one=True)[0][0]["Plan"]
            exec_time_ms = (time.time() - start_time) * 1000
            result = exec_time_ms, plan
        except Exception as e:
            logging.error(f"{query.nr}, {e}")
            self._connection.rollback()
            result = None, self._get_plan(query)
        # Disable timeout
        self._cursor.execute("set statement_timeout = 0")
        self._cleanup_query(query)
        return result

    def exec_fetchall(self, query):
        self._cursor.execute(query)
        return self._cursor.fetchall()

    def _cleanup_query(self, query):
        for query_statement in query.text.split(";"):
            if "drop view" in query_statement:
                self.exec_only(query_statement)
                self.commit()

    def _get_cost(self, query):
        query_plan = self._get_plan(query)
        total_cost = query_plan["Total Cost"]
        return total_cost

    def _get_plan(self, query):
        query_text = self._prepare_query(query)
        statement = f"explain (format json) {query_text}"
        query_plan = self.exec_fetch(statement)[0][0]["Plan"]
        self._cleanup_query(query)
        return query_plan

    def number_of_indexes(self):
        statement = """select count(*) from pg_indexes
                       where schemaname = 'public'"""
        result = self.exec_fetch(statement)
        return result[0]

    def table_exists(self, table_name):
        statement = f"""SELECT EXISTS (
            SELECT 1
            FROM pg_tables
            WHERE tablename = '{table_name}');"""
        result = self.exec_fetch(statement)
        return result[0]

    def database_exists(self, database_name):
        statement = f"""SELECT EXISTS (
            SELECT 1
            FROM pg_database
            WHERE datname = '{database_name}');"""
        result = self.exec_fetch(statement)
        return result[0]
