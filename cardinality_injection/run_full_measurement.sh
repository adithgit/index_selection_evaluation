#!/bin/zsh
# Serialized clean measurement run on the (quiet) pg_lab instance.
# Full 2x2: {default,calibrated} units x {default,injected} cardinalities.
# procost is applied ONLY around the calibrated pass.
# All 33 queries now have ground truth (99.65% subplan coverage); 28b/29b/30c
# were added after the parallel collection fix.
set -e
cd "$(dirname "$0")"
PG18=/Users/adithyaudayan/pg_lab/pg-build/pg-18/bin
QUERIES="1b 2c 3c 4b 5a 6e 7b 8b 9c 10a 11d 12a 13c 14c 15d 16a 17e 18b 19a 20c 21c 22d 23a 24a 25b 26b 27c 28b 29b 30c 31c 32a 33a"

# fresh CSV for the 30-query run (archive the 11q one)
[ -f data/results/measurements.csv ] && mv data/results/measurements.csv data/results/measurements_$(date +%s).csv

echo "=== DEFAULT UNITS PASS ==="
UNITS=default python3 run_measurements.py ${=QUERIES}

echo "=== CALIBRATED UNITS PASS (procost applied) ==="
$PG18/psql -p 5433 -d imdb -f ../calibration/pglab/procost_pglab_apply.sql
UNITS=calibrated python3 run_measurements.py ${=QUERIES}
$PG18/psql -p 5433 -d imdb -f ../calibration/pglab/procost_pglab_revert.sql

echo "=== DONE ==="
wc -l data/results/measurements.csv
