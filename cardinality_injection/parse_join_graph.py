"""Parse a JOB-style SQL query into its join graph.

Assumes the simple JOB query shape: a flat FROM list of `table AS alias`
entries and a WHERE clause of top-level AND-separated predicates (each
predicate may itself contain a parenthesized OR-group, e.g. the
`(mc.note LIKE '%(co-production)%' OR mc.note LIKE '%(presents)%')` clause
in 1a.sql -- that's fine, it still only touches one alias).

A predicate becomes a *join edge* iff it has the shape `alias1.col = alias2.col`
with two distinct aliases. Everything else is a *filter* attached to whichever
single alias it references.
"""

import re
import networkx as nx

FROM_RE = re.compile(r"\bFROM\b(.*?)\bWHERE\b", re.IGNORECASE | re.DOTALL)
WHERE_RE = re.compile(r"\bWHERE\b(.*?);?\s*$", re.IGNORECASE | re.DOTALL)
TABLE_RE = re.compile(r"(\w+)\s+AS\s+(\w+)", re.IGNORECASE)
JOIN_PRED_RE = re.compile(
    r"^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$"
)
ALIAS_REF_RE = re.compile(r"\b(\w+)\.\w+")


def split_top_level(clause_text, sep_word="AND"):
    """Split on a boolean keyword, ignoring anything inside parentheses and
    inside a `BETWEEN x AND y` clause (whose AND is not a boolean separator)."""
    parts = []
    depth = 0
    buf = []
    pending_between = 0  # counts BETWEEN clauses whose AND hasn't been consumed yet
    tokens = re.split(r"(\(|\)|\bBETWEEN\b|\s+" + sep_word + r"\s+)", clause_text, flags=re.IGNORECASE)
    for tok in tokens:
        if tok is None or tok == "":
            continue
        stripped = tok.strip()
        if stripped == "(":
            depth += 1
            buf.append(tok)
        elif stripped == ")":
            depth -= 1
            buf.append(tok)
        elif stripped.upper() == "BETWEEN":
            pending_between += 1
            buf.append(tok)
        elif depth == 0 and stripped.upper() == sep_word.upper():
            if sep_word.upper() == "AND" and pending_between > 0:
                pending_between -= 1
                buf.append(tok)
            else:
                parts.append("".join(buf).strip())
                buf = []
        else:
            buf.append(tok)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def parse_query(sql):
    """Return (alias_to_table, graph, filters).

    alias_to_table: dict alias -> real table name
    graph: networkx.Graph, nodes=aliases, edge attr 'predicates' = list[str]
    filters: dict alias -> list[str] of non-join predicates on that alias
    """
    from_match = FROM_RE.search(sql)
    where_match = WHERE_RE.search(sql)
    if not from_match or not where_match:
        raise ValueError("Query must have a FROM ... WHERE ... shape")

    from_clause = from_match.group(1)
    where_clause = where_match.group(1)

    alias_to_table = {
        alias: table for table, alias in TABLE_RE.findall(from_clause)
    }

    graph = nx.Graph()
    graph.add_nodes_from(alias_to_table.keys())

    filters = {alias: [] for alias in alias_to_table}

    predicates = split_top_level(where_clause, "AND")
    for pred in predicates:
        pred = pred.strip().rstrip(",")
        if not pred:
            continue
        m = JOIN_PRED_RE.match(pred)
        if m:
            a1, _, a2, _ = m.groups()
            if a1 in alias_to_table and a2 in alias_to_table and a1 != a2:
                if graph.has_edge(a1, a2):
                    graph[a1][a2]["predicates"].append(pred)
                else:
                    graph.add_edge(a1, a2, predicates=[pred])
                continue
        # not a simple cross-alias equality -> filter predicate(s)
        aliases_referenced = sorted(set(ALIAS_REF_RE.findall(pred)))
        aliases_referenced = [a for a in aliases_referenced if a in alias_to_table]
        if len(aliases_referenced) == 1:
            filters[aliases_referenced[0]].append(pred)
        elif len(aliases_referenced) > 1:
            # a predicate spanning multiple aliases that isn't a plain equi-join
            # (e.g. a range/inequality join) -- still needs to travel with the
            # join edge(s) between those aliases so subplan counts stay correct.
            for a1, a2 in zip(aliases_referenced, aliases_referenced[1:]):
                if graph.has_edge(a1, a2):
                    graph[a1][a2]["predicates"].append(pred)
                else:
                    graph.add_edge(a1, a2, predicates=[pred])
        # predicates with 0 aliases (shouldn't happen) are dropped silently

    return alias_to_table, graph, filters


def describe(alias_to_table, graph, filters):
    lines = []
    lines.append("Aliases:")
    for alias, table in alias_to_table.items():
        lines.append(f"  {alias} -> {table}")
    lines.append("Join edges:")
    for a1, a2, data in graph.edges(data=True):
        for p in data["predicates"]:
            lines.append(f"  {a1} -- {a2} : {p}")
    lines.append("Filters:")
    for alias, preds in filters.items():
        for p in preds:
            lines.append(f"  {alias}: {p}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    query_1a = """
    SELECT MIN(mc.note) AS production_note,
           MIN(t.title) AS movie_title,
           MIN(t.production_year) AS movie_year
    FROM company_type AS ct,
         info_type AS it,
         movie_companies AS mc,
         movie_info_idx AS mi_idx,
         title AS t
    WHERE ct.kind = 'production companies'
      AND it.info = 'top 250 rank'
      AND mc.note NOT LIKE '%(as Metro-Goldwyn-Mayer Pictures)%'
      AND (mc.note LIKE '%(co-production)%'
           OR mc.note LIKE '%(presents)%')
      AND ct.id = mc.company_type_id
      AND t.id = mc.movie_id
      AND t.id = mi_idx.movie_id
      AND mc.movie_id = mi_idx.movie_id
      AND it.id = mi_idx.info_type_id;
    """

    sql = open(sys.argv[1]).read() if len(sys.argv) > 1 else query_1a
    alias_to_table, graph, filters = parse_query(sql)
    print(describe(alias_to_table, graph, filters))
