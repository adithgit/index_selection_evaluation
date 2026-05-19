import logging
import jaydebeapi

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
        self.jar_path = "db2jcc4.jar"
        
        if not self.db_name:
            self.db_name = "imdb"
            
        # We don't call create_connection() here automatically yet 
        # so you don't get an immediate crash if the DB isn't running!
        logging.debug("DB2 connector created: {}".format(db_name))

    def create_connection(self):
        if self._connection:
            self.close()
            
        url = f"jdbc:db2://{self.host}:{self.port}/{self.db_name}"
        
        logging.info(f"Connecting to DB2 via JDBC at {url}...")
        self._connection = jaydebeapi.connect(
            "com.ibm.db2.jcc.DB2Driver",
            url,
            [self.user, self.password],
            self.jar_path,
        )
        self._cursor = self._connection.cursor()
        logging.info("Successfully connected to DB2!")

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

    # ====================================================================
    # TODO: The following methods are the DB2-specific implementations
    # that we need to build out to make the algorithms work!
    # ====================================================================

    def drop_indexes(self):
        logging.info("Dropping indexes in DB2")
        # TODO: Query SYSCAT.INDEXES to find and drop all secondary indexes
        pass
        
    def _simulate_index(self, index):
        # TODO: Use DB2 Design Advisor / RUNSTATS virtual indexes 
        # to simulate the hypothetical index
        pass

    def _drop_simulated_index(self, identifier):
        # TODO: Clean up DB2 virtual indexes from the system catalog
        pass

    def _get_cost(self, query):
        # TODO: Run DB2's EXPLAIN facility and parse the total timeron cost
        pass

    def _get_plan(self, query):
        # TODO: Run EXPLAIN and return the JSON/Text execution plan tree
        pass
