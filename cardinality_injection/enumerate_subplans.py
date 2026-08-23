"""Enumerate all connected subgraphs of a join graph and emit a COUNT(*)
query for each one, combining that subset's join predicates and per-alias
filters -- this is the "true cardinality" query for that subplan.
"""

import itertools

import networkx as nx

from parse_join_graph import parse_query


def connected_subsets(graph):
    """All non-empty subsets S of graph.nodes() such that graph.subgraph(S)
    is connected, smallest first."""
    nodes = list(graph.nodes())
    result = []
    for r in range(1, len(nodes) + 1):
        for combo in itertools.combinations(nodes, r):
            if nx.is_connected(graph.subgraph(combo)):
                result.append(combo)
    return result


def build_count_query(alias_to_table, graph, filters, subset):
    subset = set(subset)
    from_parts = [f"{alias_to_table[a]} AS {a}" for a in sorted(subset)]

    where_parts = []
    for a1, a2, data in graph.subgraph(subset).edges(data=True):
        where_parts.extend(data["predicates"])
    for a in sorted(subset):
        where_parts.extend(filters.get(a, []))

    sql = f"SELECT COUNT(*) FROM {', '.join(from_parts)}"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    return sql + ";"


def generate_all(sql_query):
    alias_to_table, graph, filters = parse_query(sql_query)
    subsets = connected_subsets(graph)
    return [
        (subset, build_count_query(alias_to_table, graph, filters, subset))
        for subset in subsets
    ]


if __name__ == "__main__":
    import sys

    sql = open(sys.argv[1]).read()
    for subset, query in generate_all(sql):
        print(f"--- {subset} ---")
        print(query)
        print()
