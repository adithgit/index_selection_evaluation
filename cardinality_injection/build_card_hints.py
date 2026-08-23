"""Turn a {subset_key: true_count} JSON (from get_true_cardinalities.py) into
a pg_lab `/*=pg_lab= ... */` hint block of Card() hints, one per subplan.
"""

import json
import sys


def build_hint_block(cardinalities):
    lines = ["/*=pg_lab="]
    for subset_key, count in cardinalities.items():
        aliases = subset_key.split(",")
        lines.append(f" Card({' '.join(aliases)} #{count})")
    lines.append("*/")
    return "\n".join(lines)


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        cardinalities = json.load(f)
    sql = open(sys.argv[2]).read().strip()
    print(build_hint_block(cardinalities))
    print(sql)
