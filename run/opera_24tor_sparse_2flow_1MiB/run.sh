#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
BASE_RUNNER="${SCRIPT_DIR}/../opera_24tor_4flow_2MiB/run.sh"

# Keep queue, feedback, and scheduler settings identical to the denser run;
# only the traffic matrix, bytes, and release density change.
exec bash "${BASE_RUNNER}" \
    --flow-generator "${SCRIPT_DIR}/generate_flows.py" \
    --flow-size-mib 1 \
    --start-superslices 16 \
    --simtime 0.01 \
    --scheduler all \
    --rxhop-weights 8:2:1 \
    --output "${SCRIPT_DIR}/results_2x1MiB_pair16_w821_admission" \
    "$@"
