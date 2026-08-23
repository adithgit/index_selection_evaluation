"""Run every subplan's COUNT(*) query against a live Postgres DB to get its
true cardinality. Equivalent to reading "actual rows" off EXPLAIN ANALYZE for
each join node, but simpler and exact: we just execute the COUNT(*) directly
rather than materializing the whole plan.
"""

import json
import sys

import psycopg2

from enumerate_subplans import generate_all


def get_true_cardinalities(sql_query, db_name="imdb", port=None):
    conn = psycopg2.connect(dbname=db_name, port=port)
    cur = conn.cursor()
    results = {}
    for subset, query in generate_all(sql_query):
        cur.execute(query)
        (count,) = cur.fetchone()
        results[",".join(sorted(subset))] = count
    cur.close()
    conn.close()
    return results


if __name__ == "__main__":
    sql = open(sys.argv[1]).read()
    db_name = sys.argv[2] if len(sys.argv) > 2 else "imdb"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else None
    results = get_true_cardinalities(sql, db_name, port)
    for subset, count in sorted(results.items(), key=lambda kv: (kv[0].count(","), kv[0])):
        print(f"{subset:40s} {count}")

    if len(sys.argv) > 4:
        with open(sys.argv[4], "w") as f:
            json.dump(results, f, indent=2)
