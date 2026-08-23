"""Canonical locations for this experiment's inputs and outputs.

Defined in one place so the directory layout can change without editing every
script. Data lives under data/, split by kind:

  data/cardinalities/  <query>_true_cardinalities.json  -- the ground truth
  data/gaps/           <query>_skipped_subplans.json    -- subplans with no
                       29b_lower_bound_subplans.json       exact count
  data/results/        measurements.csv, ab_results.json -- measured output
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")

# inputs from elsewhere in the repo
WORKLOAD = os.path.join(REPO, "custom_workloads", "JOB_REFINED")
CALIB_UNITS = os.path.join(REPO, "calibration", "pglab", "postgres_cost_units_pglab.json")
CALIB_EXT = os.path.join(REPO, "calibration", "pglab", "calib_extended_pglab.json")

# this experiment's data
DATA = os.path.join(HERE, "data")
CARDINALITIES = os.path.join(DATA, "cardinalities")
GAPS = os.path.join(DATA, "gaps")
RESULTS = os.path.join(DATA, "results")

MEASUREMENTS = os.path.join(RESULTS, "measurements.csv")
AB_RESULTS = os.path.join(RESULTS, "ab_results.json")


def cardinalities(query):
    """Ground-truth subplan cardinalities for one query."""
    return os.path.join(CARDINALITIES, f"{query}_true_cardinalities.json")


def skipped(query):
    """Subplans whose exact count could not be computed."""
    return os.path.join(GAPS, f"{query}_skipped_subplans.json")


def query_sql(query):
    return os.path.join(WORKLOAD, f"{query}.sql")


for _d in (DATA, CARDINALITIES, GAPS, RESULTS):
    os.makedirs(_d, exist_ok=True)
