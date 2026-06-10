import json
import logging
import re
import time

import mysql.connector

from selection.database_connector import DatabaseConnector

VIDEX_HOST = "127.0.0.1"
VIDEX_PORT = 13308
VIDEX_USER = "videx"
VIDEX_PASSWORD = "password"
VIDEX_STATS_PORT = 5001

_RECONNECT_WAIT_SECS = 5
_RECONNECT_MAX_ATTEMPTS = 10


class MySQLDatabaseConnector(DatabaseConnector):
    """
    Database connector for MySQL via VIDEX (Virtual Index Engine).

    Contract alignment:
      DatabaseConnector base class expects:
        - simulate_index(index)          → calls _simulate_index(index), returns result
        - drop_simulated_index(ident)    → calls _drop_simulated_index(ident)
        - estimate_index_size(index_oid) → called with hypopg_oid (our identifier string)
        - get_cost(query)                → calls _get_cost(query), returns float
        - get_plan(query)                → calls _get_plan(query), returns dict with "Total Cost"
        - all_simulated_indexes()        → returns list of (name, ...) rows
        - drop_index(index)              → drops a REAL index by index.index_idx()

      WhatIfIndexCreation expects:
        - simulate_index(index)  result[0] = oid/identifier, result[1] = name
        - drop_simulated_index(oid)   called with index.hypopg_oid
        - estimate_index_size(oid)    called with index.hypopg_oid (the identifier)
        - all_simulated_indexes()     result[x][1] = index name (used in index_names())

      CostEvaluation expects:
        - get_cost(query)        returns float (whatif mode)
        - get_plan(query)        returns dict with "Total Cost" key (for which_indexes_utilized_and_cost)
        - exec_query(query)[0]   returns runtime float (actual_runtimes mode)
        - create_index(index)    creates real index, sets index.estimated_size
        - drop_index(index)      drops real index (inherited from DatabaseConnector)

    Architecture:
      self._connection / self._cursor       → real InnoDB database
      self._videx_connection/_videx_cursor  → VIDEX shadow database (videx_<db_name>)
    """

    def __init__(self, db_name, autocommit=False):
        DatabaseConnector.__init__(self, db_name, autocommit=autocommit)
        self.db_system = "mysql"
        self._connection = None
        self._videx_connection = None
        self._server_crashed = False

        if not self.db_name:
            self.db_name = "JOB_REFINED"

        self.videx_db_name = f"videx_{self.db_name}".lower()
        self.create_connection()
        logging.debug(f"MySQL connector created: {db_name}")

    # ─────────────────────────────────────────────
    # CONNECTION MANAGEMENT
    # ─────────────────────────────────────────────

    def _apply_session_settings(self):
        """Apply session-level settings to both cursors. Called after every connect."""
        session_stmts = [
            "SET SESSION wait_timeout=3600",
            "SET SESSION interactive_timeout=3600",
            "SET SESSION net_read_timeout=600",
            "SET SESSION net_write_timeout=600",
            # optimizer_trace causes MySQL to crash on complex JOB queries
            "SET SESSION optimizer_trace='enabled=off'",
        ]
        for stmt in session_stmts:
            self._cursor.execute(stmt)
            self._videx_cursor.execute(stmt)

        # CRITICAL: tell VIDEX plugin where the statistics server is.
        # Without this, EXPLAIN costs are based on zero-row defaults.
        self._videx_cursor.execute(
            f"SET @VIDEX_SERVER='{VIDEX_HOST}:{VIDEX_STATS_PORT}'"
        )

    def create_connection(self):
        """Open both connections (real InnoDB + VIDEX shadow)."""
        self._close_connections_quietly()

        self._connection = mysql.connector.connect(
            host=VIDEX_HOST,
            port=VIDEX_PORT,
            user=VIDEX_USER,
            password=VIDEX_PASSWORD,
            database=self.db_name,
            autocommit=self.autocommit,
            connection_timeout=600,
        )
        self._cursor = self._connection.cursor()

        self._videx_connection = mysql.connector.connect(
            host=VIDEX_HOST,
            port=VIDEX_PORT,
            user=VIDEX_USER,
            password=VIDEX_PASSWORD,
            database=self.videx_db_name,
            autocommit=True,
            connection_timeout=600,
        )
        self._videx_cursor = self._videx_connection.cursor()
        self._apply_session_settings()
        self._server_crashed = False

    def _close_connections_quietly(self):
        """Close both connections, swallowing all errors."""
        for conn in (self._connection, self._videx_connection):
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        self._connection = None
        self._videx_connection = None

    def _reconnect(self):
        """
        Reconnect with linear backoff up to _RECONNECT_MAX_ATTEMPTS.

        After a SIGSEGV MySQL needs several seconds to restart. On total failure
        self._server_crashed is set True so all callers return safe fallbacks
        instead of propagating exceptions through the benchmark.
        """
        logging.warning("MySQL: connection lost — attempting reconnect...")
        self._close_connections_quietly()

        for attempt in range(1, _RECONNECT_MAX_ATTEMPTS + 1):
            wait = _RECONNECT_WAIT_SECS * attempt
            logging.info(
                f"MySQL: reconnect attempt {attempt}/{_RECONNECT_MAX_ATTEMPTS} "
                f"(waiting {wait}s)..."
            )
            time.sleep(wait)
            try:
                self.create_connection()
                logging.info("MySQL: reconnected successfully.")
                return
            except Exception as e:
                logging.warning(f"MySQL: reconnect attempt {attempt} failed: {e}")

        logging.error(
            f"MySQL: could not reconnect after {_RECONNECT_MAX_ATTEMPTS} attempts. "
            "Marking server as crashed; remaining queries will return cost=0."
        )
        self._server_crashed = True

    def close(self):
        """Override DatabaseConnector.close() to handle dual connections."""
        self._close_connections_quietly()
        logging.debug(f"MySQL connector closed: {self.db_name}")

    def commit(self):
        """Override DatabaseConnector.commit() — guard against None connection."""
        if self._connection:
            self._connection.commit()

    def rollback(self):
        """Override DatabaseConnector.rollback() — guard against None connection."""
        if self._connection:
            self._connection.rollback()

    # ─────────────────────────────────────────────
    # SAFE VIDEX EXECUTOR
    # ─────────────────────────────────────────────

    # errno 2006 = MySQL server has gone away
    # errno 2013 = Lost connection to MySQL server during query
    # errno 2055 = Cursor is not connected
    _LOST_CONNECTION_ERRNOS = {2006, 2013, 2055}

    def _videx_execute(self, stmt, query_label=""):
        """
        Execute on the VIDEX shadow connection with reconnect-on-crash logic.
        Returns True on success, False if server is permanently down.
        """
        if self._server_crashed:
            return False

        def _try():
            self._videx_cursor.execute(stmt)

        try:
            _try()
            return True
        except mysql.connector.errors.OperationalError as e:
            if e.errno in self._LOST_CONNECTION_ERRNOS:
                logging.warning(
                    f"VIDEX lost connection (errno={e.errno}) "
                    f"on {query_label!r}, reconnecting..."
                )
                self._reconnect()
                if self._server_crashed:
                    return False
                try:
                    _try()
                    return True
                except Exception as retry_err:
                    logging.error(
                        f"VIDEX retry failed on {query_label!r}: {retry_err}"
                    )
                    self._server_crashed = True
                    return False
            else:
                raise
        except Exception as e:
            logging.error(f"VIDEX execute error on {query_label!r}: {e}")
            return False

    # ─────────────────────────────────────────────
    # QUERY TEXT ADAPTATION
    # ─────────────────────────────────────────────

    def update_query_text(self, text):
        """Adapt standard SQL to MySQL dialect."""
        text = text.replace("limit -1", "").replace("LIMIT -1", "")
        text = re.sub(
            r"interval\s+'(\d+)\s+days?'",
            lambda m: f"INTERVAL {m.group(1)} DAY",
            text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"interval\s+'(\d+)\s+months?'",
            lambda m: f"INTERVAL {m.group(1)} MONTH",
            text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"interval\s+'(\d+)\s+years?'",
            lambda m: f"INTERVAL {m.group(1)} YEAR",
            text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\bdate\s+'(\d{4}-\d{2}-\d{2})'",
            r"'\1'", text, flags=re.IGNORECASE,
        )
        text = text.strip().rstrip(";")
        return text

    # ─────────────────────────────────────────────
    # VIRTUAL INDEX SIMULATION
    # Contract: DatabaseConnector.simulate_index() calls _simulate_index(index)
    #           and stores result[0] as hypopg_oid, result[1] as hypopg_name.
    #           drop_simulated_index(oid) is called with hypopg_oid.
    #           estimate_index_size(oid) is called with hypopg_oid.
    #           all_simulated_indexes() result[x][1] = index name.
    # ─────────────────────────────────────────────

    def enable_simulation(self):
        """No-op: VIDEX shadow tables are set up during metadata collection."""
        pass

    def supports_index_simulation(self):
        return True

    def _format_index_cols(self, index):
        """Format column names for CREATE INDEX, appending (255) prefix for TEXT/BLOB types."""
        if not hasattr(self, '_col_types'):
            self._col_types = {}
        table_name = index.table()
        if table_name not in self._col_types:
            self._cursor.execute(
                f"SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA='{self.db_name}' AND TABLE_NAME='{table_name}'"
            )
            self._col_types[table_name] = {row[0].lower(): row[1].lower() for row in self._cursor.fetchall()}
            
        cols = []
        for col in index.columns:
            dt = self._col_types[table_name].get(col.name.lower(), "")
            if dt in ("text", "blob", "mediumtext", "longtext"):
                cols.append(f"`{col.name}`(255)")
            else:
                cols.append(f"`{col.name}`")
        return ", ".join(cols)

    def _simulate_index(self, index):
        """
        Create a virtual index in the VIDEX shadow database.

        Returns (identifier, index_name) where both are the index name string.
        DatabaseConnector.simulate_index() stores result[0] as index.hypopg_oid
        and result[1] as index.hypopg_name. We use the same string for both
        since MySQL has no numeric OID system.

        IMPORTANT: always returns a string pair, never (None, None).
        WhatIfIndexCreation.simulate_index() assigns result[1] to index.hypopg_name
        unconditionally, and CostEvaluation.which_indexes_utilized_and_cost() does
        `index.hypopg_name not in plan_str` which raises TypeError if hypopg_name
        is None. On failure the index simply won't appear in any EXPLAIN plan, which
        is the correct behaviour (optimizer treats it as non-existent).
        """
        table_name = index.table()
        index_name = index.index_idx()

        if self._server_crashed:
            logging.debug(
                f"_simulate_index: server crashed, returning ghost name for {index_name}"
            )
            return (index_name, index_name)

        cols = self._format_index_cols(index)

        # Avoid duplicate CREATE INDEX errors
        check = (
            f"SELECT COUNT(*) FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.videx_db_name}' "
            f"AND TABLE_NAME='{table_name}' "
            f"AND INDEX_NAME='{index_name}'"
        )
        ok = self._videx_execute(check, query_label=f"check:{index_name}")
        if not ok:
            logging.debug(
                f"_simulate_index: VIDEX unavailable for {index_name}, returning ghost name"
            )
            return (index_name, index_name)

        count = self._videx_cursor.fetchone()[0]
        if count > 0:
            logging.debug(f"VIDEX index {index_name} already exists, skipping CREATE.")
            return (index_name, index_name)

        stmt = f"CREATE INDEX `{index_name}` ON `{table_name}` ({cols})"
        ok = self._videx_execute(stmt, query_label=f"create:{index_name}")
        if not ok:
            logging.debug(
                f"_simulate_index: CREATE failed for {index_name}, returning ghost name"
            )
            return (index_name, index_name)

        logging.debug(f"VIDEX simulated index created: {index_name}")
        return (index_name, index_name)

    def _drop_simulated_index(self, identifier):
        """
        Drop a virtual VIDEX index.

        Called by DatabaseConnector.drop_simulated_index(oid) with index.hypopg_oid,
        which is the index_name string we returned from _simulate_index.
        Also called by WhatIfIndexCreation.drop_all_simulated_indexes() via the
        simulated_indexes dict keyed by hypopg_oid.
        """
        if self._server_crashed or not identifier:
            return

        # Look up which table this index lives on
        check = (
            f"SELECT TABLE_NAME FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.videx_db_name}' "
            f"AND INDEX_NAME='{identifier}' LIMIT 1"
        )
        ok = self._videx_execute(check, query_label=f"drop-check:{identifier}")
        if not ok:
            return
        row = self._videx_cursor.fetchone()
        if not row:
            logging.debug(f"VIDEX index {identifier} not found, skipping DROP.")
            return

        table_name = row[0]
        stmt = f"DROP INDEX `{identifier}` ON `{table_name}`"
        self._videx_execute(stmt, query_label=f"drop:{identifier}")
        logging.debug(f"VIDEX simulated index dropped: {identifier}")

    def all_simulated_indexes(self):
        """
        Return all non-PK indexes in the VIDEX shadow schema.

        WhatIfIndexCreation.index_names() uses result[x][1] as the index name,
        so we return (index_name, index_name) tuples to satisfy that contract
        (same pattern as _simulate_index returning identical oid and name).
        """
        if self._server_crashed:
            return []
        stmt = (
            f"SELECT INDEX_NAME, TABLE_NAME FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.videx_db_name}' "
            f"AND INDEX_NAME != 'PRIMARY' "
            f"GROUP BY INDEX_NAME, TABLE_NAME"
        )
        ok = self._videx_execute(stmt, query_label="all_simulated_indexes")
        if not ok:
            return []
        rows = self._videx_cursor.fetchall()
        # Return as (index_name, index_name) so result[x][1] == index name
        # (WhatIfIndexCreation.index_names() does [x[1] for x in indexes])
        return [(r[0], r[0]) for r in rows]

    def estimate_index_size(self, index_oid):
        """
        Estimate index size in bytes for a simulated index.

        Called by WhatIfIndexCreation.estimate_index_size(index_oid) and
        CostEvaluation.estimate_size(index) with index.hypopg_oid as the argument,
        which is the index_name string we assigned in _simulate_index.

        Uses real InnoDB table statistics for accurate size estimation.
        Returns 0 on failure (callers must handle 0 gracefully).
        """
        if self._server_crashed or not index_oid:
            return 0

        # index_oid is the index name string — look up table + columns from VIDEX shadow
        check = (
            f"SELECT TABLE_NAME, COLUMN_NAME "
            f"FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.videx_db_name}' "
            f"AND INDEX_NAME='{index_oid}' "
            f"ORDER BY SEQ_IN_INDEX"
        )
        ok = self._videx_execute(check, query_label=f"size-check:{index_oid}")
        if not ok:
            return 0
        rows = self._videx_cursor.fetchall()
        if not rows:
            return 0

        table_name = rows[0][0]
        col_names = [r[1] for r in rows]

        # Row count from real InnoDB (TABLE_ROWS is an estimate but good enough)
        self._cursor.execute(
            f"SELECT TABLE_ROWS FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{self.db_name}' AND TABLE_NAME='{table_name}'"
        )
        result = self._cursor.fetchone()
        table_rows = result[0] if result and result[0] else 0

        # Column byte widths from real schema
        col_list = ", ".join(f"'{c}'" for c in col_names)
        self._cursor.execute(
            f"SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, DATA_TYPE "
            f"FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA='{self.db_name}' AND TABLE_NAME='{table_name}' "
            f"AND COLUMN_NAME IN ({col_list})"
        )
        col_rows = self._cursor.fetchall()

        TYPE_SIZES = {
            "int": 4, "bigint": 8, "smallint": 2, "tinyint": 1,
            "float": 4, "double": 8, "decimal": 9,
            "date": 3, "datetime": 8, "timestamp": 4,
        }
        key_bytes = 0
        for _, char_len, num_prec, data_type in col_rows:
            dt = data_type.lower()
            if dt in TYPE_SIZES:
                key_bytes += TYPE_SIZES[dt]
            elif char_len:
                # VARCHAR/TEXT: cap at 255 for index prefix limit
                key_bytes += min(int(char_len), 255)
            else:
                key_bytes += 8  # safe fallback

        key_bytes = key_bytes or 8
        # B-tree leaf page estimate: key + 8 bytes InnoDB ptr, 70% fill, 16KB pages
        entries_per_page = (16384 * 0.7) / (key_bytes + 8)
        leaf_pages = max(1, table_rows / entries_per_page)
        estimated_bytes = int(leaf_pages * 16384)

        logging.debug(
            f"estimate_index_size({index_oid}): table={table_name}, "
            f"rows={table_rows}, key_bytes={key_bytes}, "
            f"estimate={estimated_bytes / 1024 / 1024:.1f} MB"
        )
        return estimated_bytes

    # ─────────────────────────────────────────────
    # COST ESTIMATION
    # Contract: _get_cost returns float; _get_plan returns {"Total Cost": float}
    # Both called via DatabaseConnector.get_cost() / get_plan() which add timing.
    # ─────────────────────────────────────────────

    def _get_cost(self, query):
        """
        Run EXPLAIN FORMAT=JSON on the VIDEX shadow db and return query_cost.

        Called by DatabaseConnector.get_cost(query) which wraps it with timing
        and increments self.cost_estimations. Returns 0.0 on any failure so
        the benchmark run continues past a crashed VIDEX server.
        """
        if self._server_crashed:
            logging.warning(
                f"MySQL server crashed; returning cost=0 for query {query.nr}"
            )
            return 0.0

        query_text = self._prepare_query(query)
        if not query_text:
            return 0.0
        query_text = self.update_query_text(query_text)
        stmt = f"EXPLAIN FORMAT=JSON {query_text}"

        ok = self._videx_execute(stmt, query_label=str(query.nr))
        if not ok:
            logging.error(
                f"_get_cost: VIDEX unavailable for query {query.nr}, returning 0.0"
            )
            return 0.0

        try:
            result = self._videx_cursor.fetchone()
            if result:
                plan = json.loads(result[0])
                return float(plan["query_block"]["cost_info"]["query_cost"])
        except Exception as e:
            logging.error(f"_get_cost: parse error for query {query.nr}: {e}")

        return 0.0

    def _get_plan(self, query):
        """
        Return EXPLAIN result as a dict with a "Total Cost" key.

        Called by DatabaseConnector.get_plan(query).
        CostEvaluation.which_indexes_utilized_and_cost() reads plan["Total Cost"]
        and also searches plan_str for index names. We return the full EXPLAIN
        JSON as a nested dict so index name matching works correctly.
        """
        if self._server_crashed:
            return {"Total Cost": 0.0}

        query_text = self._prepare_query(query)
        if not query_text:
            return {"Total Cost": 0.0}
        query_text = self.update_query_text(query_text)
        stmt = f"EXPLAIN FORMAT=JSON {query_text}"

        ok = self._videx_execute(stmt, query_label=f"plan:{query.nr}")
        if not ok:
            return {"Total Cost": 0.0}

        try:
            result = self._videx_cursor.fetchone()
            if result:
                plan = json.loads(result[0])
                cost = float(plan["query_block"]["cost_info"]["query_cost"])
                # Embed cost at top level and keep full plan for index-name searching
                plan["Total Cost"] = cost
                return plan
        except Exception as e:
            logging.error(f"_get_plan: parse error for query {query.nr}: {e}")

        return {"Total Cost": 0.0}

    # ─────────────────────────────────────────────
    # ACTUAL QUERY EXECUTION
    # Contract: exec_query(query)[0] = runtime in ms (or None on failure)
    #           Used by CostEvaluation in actual_runtimes mode.
    # ─────────────────────────────────────────────

    def exec_query(self, query, timeout=None, cost_evaluation=False):
        """Execute query on the real InnoDB database and return (runtime_ms, {})."""
        if self._connection is None:
            if self._server_crashed:
                logging.warning(
                    f"exec_query: server crashed, skipping query {query.nr}"
                )
                return None, {}
            self._reconnect()
            if self._connection is None:
                return None, {}

        if not cost_evaluation:
            try:
                self._connection.commit()
            except Exception:
                self._reconnect()
                if self._connection is None:
                    return None, {}

        query_text = self._prepare_query(query)
        if not query_text:
            return None, {}
        query_text = self.update_query_text(query_text)

        if timeout:
            # MySQL optimizer hint: MAX_EXECUTION_TIME is in milliseconds
            query_text = re.sub(
                r"^\s*SELECT\s",
                f"SELECT /*+ MAX_EXECUTION_TIME({int(timeout)}) */ ",
                query_text, count=1, flags=re.IGNORECASE,
            )

        start_time = time.time()
        for attempt in range(3):
            try:
                self._cursor.execute(query_text)
                self._cursor.fetchall()
                return (time.time() - start_time) * 1000, {}
            except mysql.connector.errors.OperationalError as e:
                if e.errno in self._LOST_CONNECTION_ERRNOS:
                    logging.warning(f"Query {query.nr}: lost connection on attempt {attempt+1}, reconnecting and retrying...")
                    self._reconnect()
                    continue
                else:
                    logging.error(f"Query {query.nr} failed: {e}")
                    return None, {}
            except Exception as e:
                logging.error(f"Query {query.nr} failed: {e}")
                try:
                    self._connection.rollback()
                except Exception:
                    pass
                return None, {}
        logging.error(f"Query {query.nr} failed after 3 reconnect attempts.")
        return None, {}

    def exec_fetchall(self, query):
        self._cursor.execute(query)
        return self._cursor.fetchall()

    # ─────────────────────────────────────────────
    # REAL INDEX MANAGEMENT
    # Contract: create_index(index) sets index.estimated_size
    #           drop_index(index) inherited from DatabaseConnector — uses index.index_idx()
    #           drop_indexes() drops ALL non-PK indexes (called before each algorithm run)
    # ─────────────────────────────────────────────

    def create_index(self, index):
        """
        Create a real InnoDB index for actual_runtimes cost estimation mode.
        Sets index.estimated_size from information_schema after creation.
        Called by CostEvaluation._simulate_or_create_index() in actual_runtimes mode.

        Error 1061 (duplicate key name) is treated as a non-fatal warning:
        the index already exists from a prior run that wasn't fully cleaned up.
        We still query its size so estimated_size is populated correctly.
        """
        table_name = index.table()
        index_name = index.index_idx()
        cols = self._format_index_cols(index)
        try:
            self._cursor.execute(
                f"CREATE INDEX `{index_name}` ON `{table_name}` ({cols})"
            )
            self.commit()
        except mysql.connector.errors.DatabaseError as e:
            if e.errno == 1061:
                # Index already exists — treat as success, just log at debug level
                logging.debug(
                    f"create_index: {index_name} already exists on {table_name}, skipping."
                )
            else:
                logging.warning(f"Failed to create real index {index_name}: {e}")
                try:
                    self._connection.rollback()
                except Exception:
                    pass
                index.estimated_size = 0
                return
        except Exception as e:
            logging.warning(f"Failed to create real index {index_name}: {e}")
            try:
                self._connection.rollback()
            except Exception:
                pass
            index.estimated_size = 0
            return

        # Query size regardless of whether we just created it or it already existed
        try:
            self._cursor.execute(
                f"SELECT INDEX_LENGTH FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA='{self.db_name}' AND TABLE_NAME='{table_name}'"
            )
            result = self._cursor.fetchone()
            index.estimated_size = int(result[0]) if result and result[0] else 0
        except Exception as e:
            logging.warning(f"Could not query index size for {index_name}: {e}")
            index.estimated_size = 0

    def drop_index(self, index):
        """
        Drop a single real InnoDB index.
        Overrides DatabaseConnector.drop_index() to use backtick quoting
        and MySQL-style DROP INDEX syntax (requires ON <table>).
        Called by CostEvaluation._unsimulate_or_drop_index() in actual_runtimes mode.
        """
        index_name = index.index_idx()
        table_name = index.table()
        stmt = f"DROP INDEX `{index_name}` ON `{table_name}`"
        try:
            self._cursor.execute(stmt)
            self.commit()
        except Exception as e:
            logging.debug(f"Failed to drop index {index_name}: {e}")
            self.rollback()

    def _drop_all_videx_indexes(self):
        """
        Drop ALL non-PK indexes from the VIDEX shadow database.

        Called as part of drop_indexes() to ensure the shadow db is clean
        before each algorithm run. Without this, orphaned virtual indexes
        from a prior crashed or incomplete run accumulate and cause
        'Duplicate key name' errors when the benchmark phase calls create_index().
        """
        if self._server_crashed:
            return
        ok = self._videx_execute(
            f"SELECT TABLE_NAME, INDEX_NAME "
            f"FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.videx_db_name}' "
            f"AND INDEX_NAME != 'PRIMARY' "
            f"GROUP BY TABLE_NAME, INDEX_NAME",
            query_label="drop_all_videx_indexes"
        )
        if not ok:
            return
        rows = self._videx_cursor.fetchall()
        for table_name, index_name in rows:
            self._videx_execute(
                f"DROP INDEX `{index_name}` ON `{table_name}`",
                query_label=f"drop-videx:{index_name}"
            )
        logging.debug(f"MySQL: Dropped {len(rows)} VIDEX shadow indexes")

    def drop_indexes(self):
        """
        Drop ALL non-PK indexes from both the real InnoDB database and the
        VIDEX shadow database.

        Called by IndexSelection._run_algorithms() and SelectionAlgorithm.__init__()
        before each algorithm run to ensure a clean baseline.

        The VIDEX shadow cleanup is essential: without it, virtual indexes from
        a prior run persist in the shadow db and cause error 1061 (duplicate key)
        when the benchmark phase tries to create the same real indexes.
        """
        logging.info("MySQL: Dropping all real non-primary indexes")
        for attempt in range(3):
            try:
                self._cursor.execute(
                    f"SELECT TABLE_NAME, INDEX_NAME "
                    f"FROM information_schema.STATISTICS "
                    f"WHERE TABLE_SCHEMA='{self.db_name}' "
                    f"AND INDEX_NAME != 'PRIMARY' "
                    f"GROUP BY TABLE_NAME, INDEX_NAME"
                )
                break
            except mysql.connector.errors.OperationalError as e:
                if attempt < 2 and getattr(e, 'errno', 0) in self._LOST_CONNECTION_ERRNOS:
                    logging.warning(f"MySQL connection lost during drop_indexes (attempt {attempt+1}), reconnecting...")
                    self._reconnect()
                else:
                    raise
        for table_name, index_name in self._cursor.fetchall():
            try:
                self._cursor.execute(
                    f"DROP INDEX `{index_name}` ON `{table_name}`"
                )
            except Exception as e:
                logging.warning(f"Could not drop index {index_name}: {e}")
        self.commit()

        # Also sweep VIDEX shadow to prevent duplicate key errors in benchmark phase
        self._drop_all_videx_indexes()

    def indexes_size(self):
        """Return total index size in bytes across all tables in the real database."""
        self._cursor.execute(
            f"SELECT SUM(INDEX_LENGTH) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{self.db_name}'"
        )
        result = self._cursor.fetchone()
        return result[0] if result and result[0] else 0

    # ─────────────────────────────────────────────
    # STATISTICS & MISC
    # ─────────────────────────────────────────────

    def create_statistics(self):
        """
        Run ANALYZE TABLE on the real database to keep InnoDB statistics fresh.
        Called by IndexSelection._run_algorithms() once before algorithm runs.
        VIDEX uses pre-collected JSON histograms from its own metadata collection step.
        """
        logging.info("MySQL: Running ANALYZE TABLE on real database")
        self._cursor.execute(
            f"SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{self.db_name}'"
        )
        for (table,) in self._cursor.fetchall():
            try:
                self._cursor.execute(f"ANALYZE TABLE `{table}`")
                self._cursor.fetchall()  # ANALYZE returns a result set; must consume it
            except Exception as e:
                logging.warning(f"ANALYZE TABLE {table} failed: {e}")

    def set_random_seed(self, value=0.17):
        """
        No-op: MySQL has no session-level setseed equivalent.
        Called by IndexSelection._run_algorithms() for deterministic statistics.
        """
        pass

    def database_names(self):
        self._cursor.execute("SHOW DATABASES")
        return [row[0] for row in self._cursor.fetchall()]

    def table_exists(self, table_name):
        self._cursor.execute(
            f"SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{self.db_name}' AND TABLE_NAME='{table_name}'"
        )
        return self._cursor.fetchone()[0] > 0

    def database_exists(self, database_name):
        self._cursor.execute(
            f"SELECT COUNT(*) FROM information_schema.SCHEMATA "
            f"WHERE SCHEMA_NAME='{database_name}'"
        )
        return self._cursor.fetchone()[0] > 0

    def number_of_indexes(self):
        self._cursor.execute(
            f"SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{self.db_name}' AND INDEX_NAME != 'PRIMARY'"
        )
        return self._cursor.fetchone()[0]

    def create_database(self, database_name):
        self._cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        logging.info(f"Database {database_name} created")

    def drop_database(self, database_name):
        self._cursor.execute(f"DROP DATABASE IF EXISTS `{database_name}`")
        logging.info(f"Database {database_name} dropped")

    def import_data(self, table, path, delimiter=","):
        self._cursor.execute(
            f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE `{table}` "
            f"FIELDS TERMINATED BY '{delimiter}' "
            f"OPTIONALLY ENCLOSED BY '\"' "
            f"LINES TERMINATED BY '\\n'"
        )
        self.commit()