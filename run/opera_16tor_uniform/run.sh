#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/opera_16tor_4host_15us.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"

SIMTIME=0.05
UTILTIME=0.1
FLOW_SIZE_MIB=32
START_MODE=cycle_spread
BASE_START_NS=1000
CREDIT_QUEUE=16
SHAPING_QUEUE=8
DATA_QUEUE=600
AEOLUS_QUEUE=8
TENTATIVE_QUEUE=2
OUTPUT_DIR="${SCRIPT_DIR}/results_32MiB_cycle_spread"
BUILD=auto
SHAPING_ENABLED=yes

usage() {
    cat <<'EOF'
Usage: bash run/opera_16tor_uniform/run.sh [options]

Options:
  --flow-size-mib MIB       Size of each of the 64 flows (default: 32)
  --start-mode MODE         cycle_spread or synchronized (default: cycle_spread)
  --base-start-ns NS        Earliest flow start time (default: 1000)
  --simtime SECONDS         Simulation duration (default: 0.05)
  --utiltime MS             Utilization sampling interval (default: 0.1)
  --credq PACKETS           Credit queue capacity (default: 16)
  --qshaping PACKETS        Probabilistic shaping threshold (default: 8)
  --queue PACKETS           Data queue capacity (default: 600)
  --aeolus PACKETS          Unscheduled-data allowance (default: 8)
  --tent PACKETS            Tentative credit threshold (default: 2)
  --probfile FILE           Credit hop-probability file
  --no-shaping              Disable probabilistic admission shaping
  --output DIR              Result directory
  --build                   Always build the dynamic Opera executable
  --no-build                Require an existing executable
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --flow-size-mib) FLOW_SIZE_MIB="$2"; shift 2 ;;
        --start-mode)
            case "$2" in
                cycle_spread|synchronized) START_MODE="$2" ;;
                *) echo "--start-mode must be cycle_spread or synchronized" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --base-start-ns) BASE_START_NS="$2"; shift 2 ;;
        --simtime) SIMTIME="$2"; shift 2 ;;
        --utiltime) UTILTIME="$2"; shift 2 ;;
        --credq) CREDIT_QUEUE="$2"; shift 2 ;;
        --qshaping) SHAPING_QUEUE="$2"; shift 2 ;;
        --queue) DATA_QUEUE="$2"; shift 2 ;;
        --aeolus) AEOLUS_QUEUE="$2"; shift 2 ;;
        --tent) TENTATIVE_QUEUE="$2"; shift 2 ;;
        --probfile) PROBFILE="$2"; shift 2 ;;
        --no-shaping) SHAPING_ENABLED=no; shift ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -f "${TOPOLOGY}" ]]; then
    echo "Topology not found: ${TOPOLOGY}" >&2
    exit 1
fi

if [[ "${BUILD}" == yes || ( "${BUILD}" == auto && ! -x "${SIMULATOR}" ) ]]; then
    echo "Building dynamic Opera simulator..."
    make -C "${ROOT_DIR}/src/opera"
    make -C "${ROOT_DIR}/src/opera/datacenter" htsim_xpass_dynexpTopology
elif [[ ! -x "${SIMULATOR}" ]]; then
    echo "Simulator not found: ${SIMULATOR}" >&2
    exit 1
fi

mkdir -p "${OUTPUT_DIR}/traffic"
FLOWFILE="${OUTPUT_DIR}/traffic/uniform.htsim"
STDOUT_LOG="${OUTPUT_DIR}/uniform.log"
HTSIM_LOG="${OUTPUT_DIR}/uniform.htsim"

python3 "${SCRIPT_DIR}/generate_flows.py" \
    --output "${FLOWFILE}" \
    --flow-size-mib "${FLOW_SIZE_MIB}" \
    --start-mode "${START_MODE}" \
    --base-start-ns "${BASE_START_NS}"

if [[ "${SHAPING_ENABLED}" == yes ]]; then
    if [[ ! -f "${PROBFILE}" ]]; then
        echo "Probability file not found: ${PROBFILE}" >&2
        exit 1
    fi
    PROB_ARGS=(-probfile "${PROBFILE}")
    echo "Credit shaping enabled: credq=${CREDIT_QUEUE}, qshaping=${SHAPING_QUEUE}"
else
    SHAPING_QUEUE="${CREDIT_QUEUE}"
    PROB_ARGS=()
    echo "Credit shaping disabled; only queue overflow can reject credits"
fi

COMMAND=(
    "${SIMULATOR}"
    -flare
    -simtime "${SIMTIME}"
    -utiltime "${UTILTIME}"
    -cwnd 40
    -q "${DATA_QUEUE}"
    -credq "${CREDIT_QUEUE}"
    -qshaping "${SHAPING_QUEUE}"
    -aeolus "${AEOLUS_QUEUE}"
    -tent "${TENTATIVE_QUEUE}"
    -winit 1.0
    -tloss 0.1
    -fbw 1.2
    -jita 4
    -jitb 16
    -fbsens
    "${PROB_ARGS[@]}"
    -topfile "${TOPOLOGY}"
    -flowfile "${FLOWFILE}"
    -o "${HTSIM_LOG}"
)

printf '%q ' "${COMMAND[@]}" > "${OUTPUT_DIR}/command.txt"
printf '\n' >> "${OUTPUT_DIR}/command.txt"
echo "Running 64 balanced flows for ${SIMTIME}s..."
"${COMMAND[@]}" > "${STDOUT_LOG}" 2>&1

python3 "${SCRIPT_DIR}/analyze.py" "${OUTPUT_DIR}" --simtime "${SIMTIME}"
