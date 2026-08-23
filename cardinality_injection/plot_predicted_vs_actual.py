"""Render the predicted-vs-actual runtime comparison as a shareable PNG.

Reads measurements.csv, converts calibrated cost units to ms via the
calibration anchor (seq_page_cost = 6.166 us/unit), and plots predicted
alongside actual for the calibrated and calibrated+injected conditions.
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
MS = 0.00616602  # ms per cost unit, from calibration/pglab/postgres_cost_units_pglab.json

rows = list(csv.DictReader(open(paths.MEASUREMENTS)))
d = {(r["query"], r["units"], r["cards"]): (float(r["est_cost"]), float(r["actual_ms"]))
     for r in rows}

order = ["5a", "10a", "6e", "8b", "4b", "3c", "32a", "1b", "18b", "2c", "17e"]
pred_cal = [d[(q, "calibrated", "default")][0] * MS for q in order]
act_cal = [d[(q, "calibrated", "default")][1] for q in order]
pred_inj = [d[(q, "calibrated", "injected")][0] * MS for q in order]
act_inj = [d[(q, "calibrated", "injected")][1] for q in order]

x = np.arange(len(order))
w = 0.2

fig, ax = plt.subplots(figsize=(12, 6))
ax.bar(x - 1.5 * w, pred_cal, w, label="Predicted (calibrated)", color="#9fe1cb")
ax.bar(x - 0.5 * w, act_cal, w, label="Actual (calibrated)", color="#1baf7a")
ax.bar(x + 0.5 * w, pred_inj, w, label="Predicted (cal + injected)", color="#f5c4b3")
ax.bar(x + 1.5 * w, act_inj, w, label="Actual (cal + injected)", color="#eb6834")

ax.set_yscale("log")
ax.set_ylim(30, 6000)
ax.set_ylabel("Runtime (ms, log scale)", fontsize=11)
ax.set_xlabel("JOB query", fontsize=11)
ax.set_xticks(x)
ax.set_xticklabels(order)
ax.legend(fontsize=10, ncol=2, framealpha=0.95)
ax.grid(axis="y", color="#c9c9c9", linewidth=0.6, alpha=0.6)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)

fig.tight_layout()
out = os.path.join(HERE, "predicted_vs_actual.png")
fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
print("wrote", out)
