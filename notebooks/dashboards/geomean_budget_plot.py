import math
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ── Data (geomean across 33 JOB-refined queries per algo/budget/db) ──────────
BUDGETS = [250, 500, 1000, 2500, 5000, 7500, 10000, 15000]
BUDGET_LABELS = ["250MB", "500MB", "1GB", "2.5GB", "5GB", "7.5GB", "10GB", "15GB"]

GEOMEANS = {
    "extend": {
        "postgres": [(1.552, 1.260), (2.424, 2.805), (4.969, 4.033), (35.549, 5.901),
                     (38.091, 6.569), (38.091, 5.888), (38.091, 6.831), (32.294, 4.951)],
        "db2":      [(1.827, 0.950), (1.839, 1.050), (2.157, 1.122), (2.157, 1.028),
                     (2.157, 1.044), (2.157, 1.052), (2.157, 1.097), (2.157, 1.036)],
        "mysql":    [(1.596, 0.121), (1.632, 0.119), (4.342, 0.989), (4.342, 0.971),
                     (4.342, 0.908), (4.342, 0.890), (4.342, 0.971), (27.314, 0.880)],
    },
    "db2advis": {
        "postgres": [(1.266, 1.299), (1.886, 2.488), (6.102, 3.707), (41.701, 2.354),
                     (45.048, 8.363), (44.270, 4.280), (46.485, 9.882), (46.485, 7.488)],
        "db2":      [(1.510, 0.920), (1.892, 1.032), (2.278, 0.921), (2.260, 1.724),
                     (2.265, 1.820), (2.408, 3.131), (2.387, 2.658), (2.250, 3.303)],
        # MySQL db2advis cost estimates diverge to trillions (broken VIDEX cardinality);
        # cost axis is clipped to 1e4 for legibility — runtime values are real.
        "mysql":    [(1e3, 0.106), (1e4, 0.194), (1e4, 0.202), (1e4, 0.475),
                     (1e4, 0.776), (1e4, 2.202), (1e4, 1.880), (1e4, 2.190)],
    },
}

DB_STYLE = {
    "postgres": dict(color="#4e79a7", marker="o", label="PostgreSQL"),
    "db2":      dict(color="#f28e2b", marker="s", label="DB2"),
    "mysql":    dict(color="#e15759", marker="^", label="MySQL"),
}

ALGO_TITLE = {"extend": "EXTEND", "db2advis": "DB2Advis"}

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=False)
fig.suptitle(
    "Geometric-mean cost speedup vs actual runtime speedup\nacross all budgets (JOB-refined, 33 queries)",
    fontsize=13, y=1.02,
)

for ax, algo in zip(axes, ["extend", "db2advis"]):
    for db, pts in GEOMEANS[algo].items():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        st = DB_STYLE[db]
        ax.plot(xs, ys, color=st["color"], marker=st["marker"],
                label=st["label"], linewidth=1.6, markersize=7, zorder=3)
        # label first and last budget
        for i in (0, -1):
            ax.annotate(BUDGET_LABELS[i if i == 0 else len(BUDGETS)-1],
                        (xs[i], ys[i]), textcoords="offset points",
                        xytext=(5, 4), fontsize=7.5, color=st["color"])

    # reference lines: y=1 (no speedup), x=1 (optimizer sees no gain)
    ax.axhline(1, color="black", linewidth=0.8, linestyle="--", alpha=0.4)
    ax.axvline(1, color="black", linewidth=0.8, linestyle="--", alpha=0.4)
    ax.fill_between([0.5, ax.get_xlim()[1] if ax.get_xlim()[1] > 1 else 1e5],
                    0, 1, alpha=0.04, color="red")   # worse-than-no-index region

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Geomean cost speedup (optimizer estimate)", fontsize=10)
    ax.set_ylabel("Geomean runtime speedup (actual)", fontsize=10)
    ax.set_title(ALGO_TITLE[algo], fontsize=11, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, which="both", linestyle=":", linewidth=0.5, alpha=0.6)

    # y-axis: label 1x clearly
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.1f}×"))

    # annotate the y=1 line
    ax.text(ax.get_xlim()[0] if ax.get_xlim()[0] > 0 else 1.1, 0.97,
            "no improvement", fontsize=7, alpha=0.5, va="top")

plt.tight_layout()
plt.savefig("geomean_budget_speedup.pdf", bbox_inches="tight")
plt.show()
print("Saved: geomean_budget_speedup.pdf")
