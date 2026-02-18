#!/bin/bash

set -euo pipefail
for YEAR in {2017..2024}; do
sed "s/YEAR_TO_FILL/${YEAR}/g" run-template.sh > $YEAR/run_${YEAR}.sh
chmod +x $YEAR/run_${YEAR}.sh
done
