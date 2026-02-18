#!/bin/bash

set -euo pipefail

# to run - we expect a file structure like:
# ..
# ├── parent
# │   ├── run-all-years.sh
# │   ├── setup-run-templates.sh
# │   └──setup-year-config.sh

# # this script is run from the parent directory.
# then after running we will have
# │   ├── 2017
# │   │   └── run_2017.sh
# │   ├── 2018
# │   │   ├── config_2018.sh
# │   │   └── run_2018.sh
# etc.


# this step will overwrite existing config files and run scripts,
# so make sure to back up any custom changes before running this.
./setup-run-templates.sh
./setup-year-config.sh

for YEAR in {2017..2021}; do
  echo "=== Starting YEAR $YEAR at $(date) ==="

    pushd "$YEAR"
   ./run_${YEAR}.sh
    popd 

    echo "=== Finished YEAR $YEAR at $(date) ==="
    echo

done
echo "All years completed."
