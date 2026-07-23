#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

# A moderate-load preset for isolating receiver Credit scheduling. Explicit
# arguments appended by the caller take precedence over these defaults.
exec bash "${SCRIPT_DIR}/run.sh" \
    --fanout 64 \
    --flow-size-mib 0.5 \
    --start-mode staggered \
    --spread-superslices 72 \
    --output "${SCRIPT_DIR}/results_sparse_f64_512KiB_stagger72" \
    "$@"
