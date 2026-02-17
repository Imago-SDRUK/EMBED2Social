#!/bin/bash

set -euo pipefail

for YEAR in {2017..2021}; do
    echo "=== Starting YEAR $YEAR at $(date) ==="
   
    pushd "$YEAR"
    ./run_${YEAR}.sh
    popd 

    echo "=== Finished YEAR $YEAR at $(date) ==="
    echo
done
echo "All years completed."
