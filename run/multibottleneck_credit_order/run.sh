#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_graphTopology"

SIMTIME=0.25
UTILTIME=0.5
FLOW_SIZE=1000000
ROUNDS=20
INTERVAL_NS=10000000
ORDER_GAP_NS=1000
CREDIT_QUEUE=16
SHAPING_QUEUE=""
DATA_QUEUE=256
OUTPUT_DIR="${SCRIPT_DIR}/results"
BUILD=yes
CASES=(short_first long_first simultaneous)
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"
TOPOLOGY="${ROOT_DIR}/topologies/multibottleneck_13tor_graph.txt"
SHAPING_ENABLED=yes

usage() {
    cat <<'EOF'
Usage: bash run/multibottleneck_credit_order/run.sh [options]

Options:
  --simtime SECONDS     Simulation duration (default: 0.25)
  --utiltime MS         Utilization sampling interval (default: 0.5)
  --flow-size BYTES     Bytes per finite flow (default: 1000000)
  --rounds COUNT        Ten-flow arrival waves (default: 20)
  --interval-ns NS      Time between waves (default: 10000000)
  --order-gap-ns NS     Short/long start-time gap (default: 1000)
  --case NAME           Run one of short_first, long_first, simultaneous
  --credq PACKETS       Credit queue capacity (default: 16)
  --qshaping PACKETS    Flare shaping threshold (default: half of --credq)
  --queue PACKETS       Data queue capacity (default: 256)
  --probfile FILE       Replace the default run/pfun_exp2.txt probability file
  --no-shaping          Disable probabilistic admission shaping
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
        --flow-size) FLOW_SIZE="$2"; shift 2 ;;
        --rounds) ROUNDS="$2"; shift 2 ;;
        --interval-ns) INTERVAL_NS="$2"; shift 2 ;;
        --order-gap-ns) ORDER_GAP_NS="$2"; shift 2 ;;
        --case)
            case "$2" in
                short_first|long_first|simultaneous) CASES=("$2") ;;
                *) echo "invalid --case: $2" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --credq) CREDIT_QUEUE="$2"; shift 2 ;;
        --qshaping) SHAPING_QUEUE="$2"; shift 2 ;;
        --queue) DATA_QUEUE="$2"; shift 2 ;;
        --probfile) PROBFILE="$2"; SHAPING_ENABLED=yes; shift 2 ;;
        --no-shaping) SHAPING_ENABLED=no; shift ;;
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

mkdir -p "${OUTPUT_DIR}/traffic"
if [[ "${SHAPING_ENABLED}" == no ]]; then
    SHAPING_QUEUE="${CREDIT_QUEUE}"
    PROB_ARGS=()
    echo "Credit shaping: disabled (overflow-only control)"
else
    if [[ -z "${SHAPING_QUEUE}" ]]; then
        SHAPING_QUEUE=$(( (CREDIT_QUEUE + 1) / 2 ))
    fi
    if [[ ! -f "${PROBFILE}" ]]; then
        echo "Probability file not found: ${PROBFILE}" >&2
        exit 1
    fi
    PROB_ARGS=(-probfile "${PROBFILE}")
    echo "Credit shaping: enabled, credq=${CREDIT_QUEUE}, qshaping=${SHAPING_QUEUE}, probfile=${PROBFILE}"
fi

for case_name in "${CASES[@]}"; do
    flowfile="${OUTPUT_DIR}/traffic/${case_name}.htsim"
    stdout_log="${OUTPUT_DIR}/${case_name}.log"
    htsim_log="${OUTPUT_DIR}/${case_name}.htsim"
    python3 "${SCRIPT_DIR}/generate_flows.py" \
        --case "${case_name}" \
        --flow-size "${FLOW_SIZE}" \
        --rounds "${ROUNDS}" \
        --interval-ns "${INTERVAL_NS}" \
        --order-gap-ns "${ORDER_GAP_NS}" \
        --output "${flowfile}"
    echo "Running multi-bottleneck case=${case_name} for ${SIMTIME}s..."
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
        -topfile "${TOPOLOGY}" \
        -flowfile "${flowfile}" \
        -o "${htsim_log}" \
        > "${stdout_log}" 2>&1
done

python3 "${SCRIPT_DIR}/analyze.py" "${OUTPUT_DIR}" \
    --simtime "${SIMTIME}" --cases "${CASES[@]}"
