#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/dynexp_55us_symm.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"

SIMTIME=0.05
UTILTIME=0.1
FLOW_SIZE_MIB=4
FLOWS_PER_HOST=8
START_SUPERSLICES=2
BASE_START_NS=1000
SCHEDULER=both
RX_HOP_QUANTUM=16
CWND=4
DATA_QUEUE=600
CREDIT_QUEUE=60
SHAPING_QUEUE=30
AEOLUS_QUEUE=40
TENTATIVE_QUEUE=4
OUTPUT_ROOT="${SCRIPT_DIR}/results_8x4MiB_hostflow_stagger2_q16"
BUILD=auto
SHAPING_ENABLED=yes

usage() {
    cat <<'EOF'
Usage: bash run/opera_108tor_8flow_4MiB/run.sh [options]

Options:
  --scheduler MODE          fifo, rxhopprio, or both (default: both)
  --rxhop-quantum CREDITS   Credits served per selected Flow (default: 16)
  --flow-size-mib MIB       Size of every flow (default: 4)
  --flows-per-host COUNT    Flows sourced/received per host (default: 8)
  --start-superslices N     Stagger starts over 1 or 2 superslices (default: 2)
  --base-start-ns NS        Earliest flow start phase (default: 1000)
  --simtime SECONDS         Simulation duration (default: 0.05)
  --utiltime MS             Utilization sampling interval (default: 0.1)
  --cwnd PACKETS            Initial unscheduled window (default: 4)
  --queue PACKETS           Data queue capacity (default: 600)
  --credq PACKETS           Credit queue capacity (default: 60)
  --qshaping PACKETS        Probabilistic shaping threshold (default: 30)
  --aeolus PACKETS          Unscheduled-data allowance (default: 40)
  --tent PACKETS            Tentative credit threshold (default: 4)
  --probfile FILE           Credit hop-probability file
  --topology FILE           Native 648-host, 108-ToR Opera topology
  --no-shaping              Disable probabilistic Credit admission shaping
  --output DIR              Root directory for both cases
  --build                   Clean-build the dynamic Opera executable
  --no-build                Require an existing executable
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scheduler)
            case "$2" in
                fifo|rxhopprio|both) SCHEDULER="$2" ;;
                *) echo "--scheduler must be fifo, rxhopprio, or both" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --flow-size-mib) FLOW_SIZE_MIB="$2"; shift 2 ;;
        --rxhop-quantum) RX_HOP_QUANTUM="$2"; shift 2 ;;
        --flows-per-host) FLOWS_PER_HOST="$2"; shift 2 ;;
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
if ! [[ "${RX_HOP_QUANTUM}" =~ ^[1-9][0-9]*$ ]]; then
    echo "--rxhop-quantum must be a positive integer" >&2
    exit 2
fi

read -r HOSTS HOSTS_PER_TOR UPLINKS TORS < <(sed -n '1p' "${TOPOLOGY}")
read -r TOPOLOGY_SLICES EPSILON_PS DELTA_PS RECONFIG_PS < <(sed -n '2p' "${TOPOLOGY}")
if [[ "${HOSTS}" -ne 648 || "${HOSTS_PER_TOR}" -ne 6 || \
      "${UPLINKS}" -ne 6 || "${TORS}" -ne 108 ]]; then
    echo "Expected the native 648-host, 108-ToR, 6+6 Opera topology" >&2
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
python3 "${SCRIPT_DIR}/generate_flows.py" \
    --output "${SHARED_FLOWFILE}" \
    --flow-size-mib "${FLOW_SIZE_MIB}" \
    --flows-per-host "${FLOWS_PER_HOST}" \
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
    if [[ "${case_name}" == rxhopprio ]]; then
        priority_args=(-rxhopprio -rxhopquantum "${RX_HOP_QUANTUM}")
    fi

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
echo "Workload: ${FLOWS_PER_HOST} x ${FLOW_SIZE_MIB} MiB per host; cwnd=${CWND}"
echo "Receiver hop-priority Flow quantum: ${RX_HOP_QUANTUM} Credits"
case "${SCHEDULER}" in
    fifo) run_case fifo ;;
    rxhopprio) run_case rxhopprio ;;
    both)
        run_case fifo
        run_case rxhopprio
        python3 "${SCRIPT_DIR}/compare.py" \
            --fifo "${OUTPUT_ROOT}/fifo/summary.csv" \
            --rxhopprio "${OUTPUT_ROOT}/rxhopprio/summary.csv" \
            --output "${OUTPUT_ROOT}/comparison.csv"
        ;;
esac
