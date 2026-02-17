#!/bin/bash

set -euo pipefail
YEAR=YEAR_TO_FILL
CONFIG=config_${YEAR}.yaml
DATA_DIR="/embed2social-storage/${YEAR}"
LOG="logs/${YEAR}.log"
SCRIPTS="/scripts/embeddings/"
mkdir -p logs
nohup bash -c "
    find \"${DATA_DIR}\" -maxdepth 1 -type f -name '*.tif' \
      | python $SCRIPTS/embed_to_lsoa.py --config_path \"${CONFIG}\"
" > "$LOG" 2>&1 

echo "Started YEAR job in background (PID $!)"
echo "Log is at $LOG"
