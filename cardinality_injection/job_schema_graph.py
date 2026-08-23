"""The full JOB/IMDB schema as a PK-FK graph (not a single query's join
graph). Nodes are tables; an edge exists between two tables iff there is a
foreign-key column in one referencing the primary key of the other.

Edge list derived from the canonical schema (gregrahn/join-order-benchmark
schema.sql + fkindexes.sql), plus the two comp_cast_type FK relationships
(complete_cast.subject_id / status_id) that aren't indexed in that repo but
are real FK relationships exercised by queries 20, 23, 26-30.
"""

import networkx as nx

FK_EDGES = [
    ("movie_companies", "company_name"),      # company_id -> company_name.id
    ("movie_companies", "company_type"),       # company_type_id -> company_type.id
    ("movie_info_idx", "info_type"),           # info_type_id -> info_type.id
    ("movie_info", "info_type"),               # info_type_id -> info_type.id
    ("person_info", "info_type"),              # info_type_id -> info_type.id
    ("movie_keyword", "keyword"),              # keyword_id -> keyword.id
    ("aka_title", "kind_type"),                # kind_id -> kind_type.id
    ("title", "kind_type"),                    # kind_id -> kind_type.id
    ("movie_link", "title"),                   # linked_movie_id -> title.id
    ("movie_link", "link_type"),               # link_type_id -> link_type.id
    ("aka_title", "title"),                    # movie_id -> title.id
    ("cast_info", "title"),                    # movie_id -> title.id
    ("complete_cast", "title"),                # movie_id -> title.id
    ("movie_companies", "title"),               # movie_id -> title.id
    ("movie_info_idx", "title"),                # movie_id -> title.id
    ("movie_keyword", "title"),                 # movie_id -> title.id
    ("movie_link", "title"),                    # movie_id -> title.id (dup edge w/ linked_movie_id, collapses)
    ("movie_info", "title"),                    # movie_id -> title.id
    ("aka_name", "name"),                       # person_id -> name.id
    ("cast_info", "name"),                      # person_id -> name.id
    ("person_info", "name"),                    # person_id -> name.id
    ("cast_info", "char_name"),                 # person_role_id -> char_name.id
    ("cast_info", "role_type"),                 # role_id -> role_type.id
    ("complete_cast", "comp_cast_type"),         # subject_id -> comp_cast_type.id
    # status_id -> comp_cast_type.id is a second, parallel FK edge between the
    # same table pair -- a plain Graph collapses it, which is fine: it doesn't
    # change node-set connectivity, only which specific columns realize it.
]


def build_schema_graph():
    g = nx.Graph()
    g.add_edges_from(FK_EDGES)
    return g


if __name__ == "__main__":
    g = build_schema_graph()
    print(f"Nodes (tables): {g.number_of_nodes()}")
    print(f"Edges (FK relationships, deduped by table pair): {g.number_of_edges()}")
    print(sorted(g.nodes()))
