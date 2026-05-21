import glob
import os

from selection.workload import Column, Query, Table, Workload
from selection.dbms.postgres_dbms import PostgresDatabaseConnector


class WorkloadParser:
    def __init__(self, database_system, database_name, benchmark_name):
        self.database_system = database_system
        self.database_name = database_name
        self.benchmark_name = benchmark_name

    @staticmethod
    def is_custom_workload(benchmark_name):
        file_path = os.path.dirname(os.path.abspath(__file__))
        if benchmark_name in os.listdir(f"{file_path}/../custom_workloads/"):
            return True
        else:
            return False

    def get_tables(self):
        if self.database_system == "postgres":
            from selection.dbms.postgres_dbms import PostgresDatabaseConnector
            db_connector = PostgresDatabaseConnector(self.database_name)
            result = db_connector.exec_fetchall(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public';"
            )
            table_names = [row[0] for row in result]

            tables = {}
            for table_name in table_names:
                table = Table(table_name)
                result = db_connector.exec_fetch(
                    "SELECT column_name "
                    + "FROM information_schema.columns "
                    + "WHERE table_schema = 'public' "
                    + f"AND table_name = '{table_name}';", False
                )
                column_names = [row[0] for row in result]
                for column_name in column_names:
                    table.add_column(Column(column_name))
                tables[table_name] = table
            return tables
        elif self.database_system == "db2":
            from selection.dbms.db2_dbms import DB2DatabaseConnector
            db_connector = DB2DatabaseConnector(self.database_name)
            db_connector.create_connection()
            schema = db_connector.user.upper()
            result = db_connector.exec_fetch(
                f"SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA='{schema}' AND TYPE='T'", False
            )
            table_names = [row[0].lower() for row in result]

            tables = {}
            for table_name in table_names:
                table = Table(table_name)
                result = db_connector.exec_fetch(
                    f"SELECT COLNAME FROM SYSCAT.COLUMNS WHERE TABSCHEMA='{schema}' AND TABNAME='{table_name.upper()}'", False
                )
                column_names = [row[0].lower() for row in result]
                for column_name in column_names:
                    table.add_column(Column(column_name))
                tables[table_name] = table
            return tables
        else:
            raise NotImplementedError(f"get_tables for {self.database_system}")

    def store_indexable_columns(self, query, tables):
        for table_name in tables:
            if table_name in query.text:
                table = tables[table_name]
                for column in table.columns:
                    if column.name in query.text:
                        query.columns.append(column)

    def execute(self):
        #processes the queries that we want to execute 
        
        file_path = os.path.dirname(os.path.abspath(__file__))
        query_files = glob.glob(
            f"{file_path}/../custom_workloads/{self.benchmark_name}/*.sql"
        )
        query_files.sort()

        # Retrieve schema to search for indexable columns
        tables = self.get_tables()

        queries = []

        for file_name in query_files:
            with open(file_name) as f:
                query_text = f.read()
                query_id = file_name.split("/")[-1]
                query = Query(query_id, query_text)
                # add the colls that query contains by going thru the tables schema
                self.store_indexable_columns(query, tables)
                queries.append(query)

        return Workload(queries)
