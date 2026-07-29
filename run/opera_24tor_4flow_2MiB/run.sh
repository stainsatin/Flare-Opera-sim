#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/opera_24tor_4host_55us.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"
FLOW_GENERATOR="${SCRIPT_DIR}/generate_flows.py"

SIMTIME=0.02
UTILTIME=0.1
FLOW_SIZE_MIB=2
START_SUPERSLICES=8
BASE_START_NS=1000
SCHEDULER=all
RX_HOP_WEIGHTS=8:2:1
CWND=4
DATA_QUEUE=600
CREDIT_QUEUE=60
SHAPING_QUEUE=30
AEOLUS_QUEUE=40
TENTATIVE_QUEUE=4
OUTPUT_ROOT="${SCRIPT_DIR}/results_4x2MiB_stagger8_w821_admission"
BUILD=auto
SHAPING_ENABLED=yes

usage() {
    cat <<'EOF'
Usage: bash run/opera_24tor_4flow_2MiB/run.sh [options]

Options:
  --scheduler MODE          fifo, wrr, admission, combined, or all (default: all)
  --rxhop-weights H:M:L     NIC Credit WRR weights (default: 8:2:1)
  --flow-size-mib MIB       Size of every flow (default: 2)
  --start-superslices N     Release over 4, 8, or 16 slices (default: 8)
  --base-start-ns NS        Earliest flow start phase (default: 1000)
  --simtime SECONDS         Simulation duration (default: 0.02)
  --utiltime MS             Utilization sampling interval (default: 0.1)
  --cwnd PACKETS            Initial unscheduled window (default: 4)
  --queue PACKETS           Data queue capacity (default: 600)
  --credq PACKETS           Shared Credit capacity (default: 60)
  --qshaping PACKETS        Probabilistic shaping threshold (default: 30)
  --aeolus PACKETS          Unscheduled-data allowance (default: 40)
  --tent PACKETS            Tentative Credit threshold (default: 4)
  --probfile FILE           Credit hop-probability file
  --flow-generator FILE     Compatible workload generator (default: local generator)
  --topology FILE           Compatible 96-host, 24-ToR Opera topology
  --no-shaping              Disable probabilistic Credit admission shaping
  --output DIR              Root directory for selected cases
  --build                   Clean-build the dynamic Opera executable
  --no-build                Require an existing executable
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scheduler)
            case "$2" in
                fifo|wrr|admission|combined|all) SCHEDULER="$2" ;;
                *) echo "--scheduler must be fifo, wrr, admission, combined, or all" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --rxhop-weights) RX_HOP_WEIGHTS="$2"; shift 2 ;;
        --flow-size-mib) FLOW_SIZE_MIB="$2"; shift 2 ;;
        --start-superslices) START_SUPERSLICES="$2"; shift 2 ;;
        --base-start-ns) BASE_START_NS="$2"; shift 2 ;;
        --simtime) SIMTIME="$2"; shift 2 ;;
        --utiltime) UTILTIME="$2"; shift 2 ;;
        --cwnd) CWND="$2"; shift 2 ;;
        --queue) DATA_QUEUE="$2"; shift 2 ;;
        --credq) CREDIT_QUEUE="$2"; shift 2 ;;
        --qshaping) SHAPING_QUEUE="$2"; shift 2 ;;
        --aeolus) AEOLUS_QUEUE="$2"; shift 2 ;;
        --tent) TENTATIVE_QUEUE="$2"; shift 2 ;;
        --probfile) PROBFILE="$2"; shift 2 ;;
        --flow-generator) FLOW_GENERATOR="$2"; shift 2 ;;
        --topology) TOPOLOGY="$2"; shift 2 ;;
        --no-shaping) SHAPING_ENABLED=no; shift ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
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
if [[ ! -f "${FLOW_GENERATOR}" ]]; then
    echo "Flow generator not found: ${FLOW_GENERATOR}" >&2
    exit 1
fi
if ! [[ "${RX_HOP_WEIGHTS}" =~ ^[1-9][0-9]*:[1-9][0-9]*:[1-9][0-9]*$ ]]; then
    echo "--rxhop-weights must be three positive integers (H:M:L)" >&2
    exit 2
fi
case "${START_SUPERSLICES}" in
    4|8|16) ;;
    *) echo "--start-superslices must be 4, 8, or 16" >&2; exit 2 ;;
esac
IFS=: read -r RX_HOP_WEIGHT_HIGH RX_HOP_WEIGHT_MEDIUM RX_HOP_WEIGHT_LOW <<< "${RX_HOP_WEIGHTS}"

read -r HOSTS HOSTS_PER_TOR UPLINKS TORS < <(sed -n '1p' "${TOPOLOGY}")
read -r TOPOLOGY_SLICES EPSILON_PS DELTA_PS RECONFIG_PS < <(sed -n '2p' "${TOPOLOGY}")
if [[ "${HOSTS}" -ne 96 || "${HOSTS_PER_TOR}" -ne 4 || \
      "${UPLINKS}" -ne 6 || "${TORS}" -ne 24 ]]; then
    echo "Expected a 96-host, 24-ToR, 4+6 Opera topology" >&2
    exit 1
fi
if [[ "${TOPOLOGY_SLICES}" -ne $((3 * TORS)) ]]; then
    echo "Expected three internal slices per superslice" >&2
    exit 1
fi
SUPERSLICE_PS=$((EPSILON_PS + DELTA_PS + RECONFIG_PS))
ACTIVE_WINDOW_PS=$((EPSILON_PS + DELTA_PS))
if (( SUPERSLICE_PS <= 0 || SUPERSLICE_PS % 1000 != 0 || \
      ACTIVE_WINDOW_PS <= 0 || ACTIVE_WINDOW_PS % 1000 != 0 )); then
    echo "Topology timing must use positive whole nanoseconds" >&2
    exit 1
fi
SUPERSLICE_NS=$((SUPERSLICE_PS / 1000))
ACTIVE_WINDOW_NS=$((ACTIVE_WINDOW_PS / 1000))
CYCLE_NS=$((SUPERSLICE_NS * TORS))

if [[ "${BUILD}" == yes || ( "${BUILD}" == auto && ! -x "${SIMULATOR}" ) ]]; then
    echo "Clean-building dynamic Opera simulator..."
    make -C "${ROOT_DIR}/src/opera/datacenter" clean
    make -C "${ROOT_DIR}/src/opera" clean
    make -C "${ROOT_DIR}/src/opera"
    make -C "${ROOT_DIR}/src/opera/datacenter" htsim_xpass_dynexpTopology
elif [[ ! -x "${SIMULATOR}" ]]; then
    echo "Simulator not found: ${SIMULATOR}" >&2
    exit 1
fi

mkdir -p "${OUTPUT_ROOT}/workload"
SHARED_FLOWFILE="${OUTPUT_ROOT}/workload/uniform.htsim"
python3 "${FLOW_GENERATOR}" \
    --output "${SHARED_FLOWFILE}" \
    --flow-size-mib "${FLOW_SIZE_MIB}" \
    --start-superslices "${START_SUPERSLICES}" \
    --base-start-ns "${BASE_START_NS}" \
    --superslice-ns "${SUPERSLICE_NS}" \
    --active-window-ns "${ACTIVE_WINDOW_NS}"
FLOW_COUNT=$(wc -l < "${SHARED_FLOWFILE}")

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
    echo "Credit shaping disabled; only queue overflow can reject Credits"
fi

run_case() {
    local case_name="$1"
    local case_dir="${OUTPUT_ROOT}/${case_name}"
    local priority_args=()
    case "${case_name}" in
        fifo) ;;
        wrr)
            priority_args=(-rxhopprio -rxhopweights \
                "${RX_HOP_WEIGHT_HIGH}" "${RX_HOP_WEIGHT_MEDIUM}" "${RX_HOP_WEIGHT_LOW}")
            ;;
        admission)
            priority_args=(-rxprioadmit)
            ;;
        combined)
            priority_args=(-rxhopprio -rxhopweights \
                "${RX_HOP_WEIGHT_HIGH}" "${RX_HOP_WEIGHT_MEDIUM}" "${RX_HOP_WEIGHT_LOW}" \
                -rxprioadmit)
            ;;
        *) echo "Unknown case: ${case_name}" >&2; return 2 ;;
    esac

    mkdir -p "${case_dir}/traffic"
    cp "${SHARED_FLOWFILE}" "${case_dir}/traffic/uniform.htsim"
    local stdout_log="${case_dir}/uniform.log"
    local htsim_log="${case_dir}/uniform.htsim"
    local command=(
        "${SIMULATOR}"
        -flare
        -simtime "${SIMTIME}"
        -utiltime "${UTILTIME}"
        -cwnd "${CWND}"
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
        "${priority_args[@]}"
        "${PROB_ARGS[@]}"
        -topfile "${TOPOLOGY}"
        -flowfile "${case_dir}/traffic/uniform.htsim"
        -o "${htsim_log}"
    )

    printf '%q ' "${command[@]}" > "${case_dir}/command.txt"
    printf '\n' >> "${case_dir}/command.txt"
    echo "Running ${case_name}: ${FLOW_COUNT} flows for ${SIMTIME}s..."
    if "${command[@]}" > "${stdout_log}" 2>&1; then
        echo "${case_name} simulation completed."
    else
        local status=$?
        echo "${case_name} failed with exit code ${status}. Last 80 lines:" >&2
        tail -n 80 "${stdout_log}" >&2
        return "${status}"
    fi

    python3 "${SCRIPT_DIR}/analyze.py" "${case_dir}" \
        --simtime "${SIMTIME}" \
        --hosts-per-tor "${HOSTS_PER_TOR}" \
        --tor-count "${TORS}" \
        --cycle-superslices "${TORS}" \
        --superslice-ns "${SUPERSLICE_NS}"
}

echo "Topology: ${TORS} ToRs, ${HOSTS_PER_TOR} hosts/ToR, ${HOSTS} hosts"
echo "Superslice: ${SUPERSLICE_NS} ns; active: ${ACTIVE_WINDOW_NS} ns; cycle: ${CYCLE_NS} ns"
echo "Workload: ${FLOW_COUNT} flows x ${FLOW_SIZE_MIB} MiB across ${START_SUPERSLICES} superslices; cwnd=${CWND}"
echo "Receiver NIC Credit modes: WRR=${RX_HOP_WEIGHTS}; priority admission is independently switchable"
case "${SCHEDULER}" in
    fifo) run_case fifo ;;
    wrr) run_case wrr ;;
    admission) run_case admission ;;
    combined) run_case combined ;;
    all)
        run_case fifo
        run_case wrr
        run_case admission
        run_case combined
        python3 "${SCRIPT_DIR}/compare.py" \
            --fifo "${OUTPUT_ROOT}/fifo/summary.csv" \
            --wrr "${OUTPUT_ROOT}/wrr/summary.csv" \
            --admission "${OUTPUT_ROOT}/admission/summary.csv" \
            --combined "${OUTPUT_ROOT}/combined/summary.csv" \
            --output "${OUTPUT_ROOT}/comparison.csv"
        ;;
esac
