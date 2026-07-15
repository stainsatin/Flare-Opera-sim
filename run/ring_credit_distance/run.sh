#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_graphTopology"

SIMTIME=0.02
UTILTIME=0.1
CREDIT_QUEUE=16
SHAPING_QUEUE=""
DATA_QUEUE=256
OUTPUT_DIR="${SCRIPT_DIR}/results"
BUILD=yes
DISTANCES=(1 2 4)
PROBFILE=""

usage() {
    cat <<'EOF'
Usage: bash run/ring_credit_distance/run.sh [options]

Options:
  --simtime SECONDS     Simulation duration (default: 0.02)
  --utiltime MS         Utilization sampling interval (default: 0.1)
  --credq PACKETS       Credit queue capacity (default: 16)
  --qshaping PACKETS    Flare shaping threshold (default: same as --credq)
  --queue PACKETS       Data queue capacity (default: 256)
  --distance K          Run only K=1, K=2, or K=4
  --probfile FILE       Enable Flare shaping with a hop probability file
  --output DIR          Result directory
  --build               Build even if the simulator binary exists
  --no-build            Never build; require an existing binary
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --simtime) SIMTIME="$2"; shift 2 ;;
        --utiltime) UTILTIME="$2"; shift 2 ;;
        --credq) CREDIT_QUEUE="$2"; shift 2 ;;
        --qshaping) SHAPING_QUEUE="$2"; shift 2 ;;
        --queue) DATA_QUEUE="$2"; shift 2 ;;
        --distance)
            case "$2" in 1|2|4) DISTANCES=("$2") ;; *) echo "--distance must be 1, 2, or 4" >&2; exit 2 ;; esac
            shift 2
            ;;
        --probfile) PROBFILE="$2"; shift 2 ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "${BUILD}" == yes ]]; then
    echo "Building Opera simulator..."
    make -C "${ROOT_DIR}/src/opera"
    make -C "${ROOT_DIR}/src/opera/datacenter" htsim_xpass_graphTopology
elif [[ ! -x "${SIMULATOR}" ]]; then
    echo "Simulator not found: ${SIMULATOR}" >&2
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"
if [[ -z "${SHAPING_QUEUE}" ]]; then
    SHAPING_QUEUE="${CREDIT_QUEUE}"
fi
PROB_ARGS=()
if [[ -n "${PROBFILE}" ]]; then
    PROB_ARGS=(-probfile "${PROBFILE}")
fi
for distance in "${DISTANCES[@]}"; do
    stdout_log="${OUTPUT_DIR}/k${distance}.log"
    htsim_log="${OUTPUT_DIR}/k${distance}.htsim"
    echo "Running ring distance k=${distance} for ${SIMTIME}s..."
    "${SIMULATOR}" \
        -flare \
        -strat single \
        -simtime "${SIMTIME}" \
        -utiltime "${UTILTIME}" \
        -cwnd 16 \
        -q "${DATA_QUEUE}" \
        -credq "${CREDIT_QUEUE}" \
        -qshaping "${SHAPING_QUEUE}" \
        -tent "${CREDIT_QUEUE}" \
        -aeolus "${DATA_QUEUE}" \
        -winit 1.0 \
        -tloss 0.1 \
        -fbw 1.2 \
        "${PROB_ARGS[@]}" \
        -topfile "${ROOT_DIR}/topologies/ring_8tor_graph.txt" \
        -flowfile "${ROOT_DIR}/traffic/ring_8tor_k${distance}.htsim" \
        -o "${htsim_log}" \
        > "${stdout_log}" 2>&1
done

python3 "${SCRIPT_DIR}/analyze.py" "${OUTPUT_DIR}" --distances "${DISTANCES[@]}"
