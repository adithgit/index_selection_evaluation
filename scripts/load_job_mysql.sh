#!/bin/bash
# load_job_mysql.sh
# Loads all JOB CSVs into the VIDEX MySQL container (port 13308)
# Run from the root of index_selection_evaluation/

set -e

MYSQL_HOST="127.0.0.1"
MYSQL_PORT="13308"
MYSQL_USER="videx"
MYSQL_PASS="password"
DB="JOB_REFINED"
DATADIR="$(pwd)/jobdata"
SCHEMA="$DATADIR/schema_mysql.sql"

echo "==> [1/3] Creating schema..."
mysql -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p$MYSQL_PASS < $SCHEMA
echo "    Schema created."

echo "==> [2/3] Enabling local infile..."
# Allow LOAD DATA LOCAL INFILE from client side
mysql -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p$MYSQL_PASS \
  -e "SET GLOBAL local_infile=1;"

echo "==> [3/3] Loading data..."

TABLES=(
  aka_name aka_title cast_info char_name comp_cast_type
  company_name company_type complete_cast info_type keyword
  kind_type link_type movie_companies movie_info movie_info_idx
  movie_keyword movie_link name person_info role_type title
)

for TABLE in "${TABLES[@]}"; do
  CSV="$DATADIR/$TABLE.csv"
  if [ ! -f "$CSV" ]; then
    echo "    WARNING: $CSV not found, skipping."
    continue
  fi
  echo "    Loading $TABLE..."
  mysql -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p$MYSQL_PASS \
    --local-infile=1 $DB \
    -e "LOAD DATA LOCAL INFILE '$CSV'
        INTO TABLE \`$TABLE\`
        FIELDS TERMINATED BY ','
        OPTIONALLY ENCLOSED BY '\"'
        LINES TERMINATED BY '\n'
        ($(mysql -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p$MYSQL_PASS $DB -NB \
           -e \"SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) \
               FROM information_schema.COLUMNS \
               WHERE TABLE_SCHEMA='$DB' AND TABLE_NAME='$TABLE';\"));"
done

echo ""
echo "Done! All tables loaded into $DB."
echo "Verify with: mysql -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p$MYSQL_PASS $DB -e 'SHOW TABLE STATUS\\G'"
