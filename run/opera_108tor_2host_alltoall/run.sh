#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
SOURCE_TOPOLOGY="${ROOT_DIR}/topologies/dynexp_55us_symm.txt"
GENERATED_TOPOLOGY="${SCRIPT_DIR}/generated/opera_108tor_2host_55us.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"

SIMTIME=0.1
UTILTIME=0.1
FLOW_SIZE_MIB=1
FANOUT=107
START_MODE=cycle_spread
START_STRIDE=53
SPREAD_SUPERSLICES=108
BASE_START_NS=1000
SCHEDULER=both
CWND=40
DATA_QUEUE=600
CREDIT_QUEUE=60
SHAPING_QUEUE=30
AEOLUS_QUEUE=40
TENTATIVE_QUEUE=4
OUTPUT_ROOT="${SCRIPT_DIR}/results_1MiB_cycle_spread"
BUILD=auto
SHAPING_ENABLED=yes

usage() {
    cat <<'EOF'
Usage: bash run/opera_108tor_2host_alltoall/run.sh [options]

Options:
  --scheduler MODE          fifo, rxhopprio, or both (default: both)
  --flow-size-mib MIB       Size of every flow (default: 1)
  --fanout COUNT            Remote ToRs contacted by each host, 1..107 (default: 107)
  --start-mode MODE         cycle_spread, staggered, or synchronized
  --start-stride COUNT      Staggered receiver phase stride (default: 53)
  --spread-superslices N    Staggered release-window length (default: 108)
  --base-start-ns NS        Earliest flow start time (default: 1000)
  --simtime SECONDS         Simulation duration (default: 0.1)
  --utiltime MS             Utilization sampling interval (default: 0.1)
  --cwnd PACKETS            Initial unscheduled window (default: 40)
  --queue PACKETS           Data queue capacity (default: 600)
  --credq PACKETS           Credit queue capacity (default: 60)
  --qshaping PACKETS        Probabilistic shaping threshold (default: 30)
  --aeolus PACKETS          Unscheduled-data allowance (default: 40)
  --tent PACKETS            Tentative credit threshold (default: 4)
  --probfile FILE           Credit hop-probability file
  --source-topology FILE    Native 648-host, 108-ToR topology
  --generated-topology FILE Derived 216-host topology output
  --no-shaping              Disable probabilistic admission shaping
  --output DIR              Root result directory
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
        --fanout) FANOUT="$2"; shift 2 ;;
        --start-mode)
            case "$2" in
                cycle_spread|staggered|synchronized) START_MODE="$2" ;;
                *) echo "--start-mode must be cycle_spread, staggered, or synchronized" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --start-stride) START_STRIDE="$2"; shift 2 ;;
        --spread-superslices) SPREAD_SUPERSLICES="$2"; shift 2 ;;
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
        --source-topology) SOURCE_TOPOLOGY="$2"; shift 2 ;;
        --generated-topology) GENERATED_TOPOLOGY="$2"; shift 2 ;;
        --no-shaping) SHAPING_ENABLED=no; shift ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -f "${SOURCE_TOPOLOGY}" ]]; then
    echo "Source topology not found: ${SOURCE_TOPOLOGY}" >&2
    exit 1
fi

python3 "${SCRIPT_DIR}/build_topology.py" \
    --source "${SOURCE_TOPOLOGY}" \
    --output "${GENERATED_TOPOLOGY}" \
    --hosts-per-tor 2

read -r HOSTS HOSTS_PER_TOR UPLINKS TORS < <(sed -n '1p' "${GENERATED_TOPOLOGY}")
read -r TOPOLOGY_SLICES EPSILON_PS DELTA_PS RECONFIG_PS < <(sed -n '2p' "${GENERATED_TOPOLOGY}")
if [[ "${HOSTS}" -ne 216 || "${HOSTS_PER_TOR}" -ne 2 || \
      "${UPLINKS}" -ne 6 || "${TORS}" -ne 108 ]]; then
    echo "Expected a 216-host, 108-ToR, 2-downlink + 6-uplink topology" >&2
    exit 1
fi
if [[ "${TOPOLOGY_SLICES}" -ne $((3 * TORS)) ]]; then
    echo "Expected three internal slices per superslice" >&2
    exit 1
fi
SUPERSLICE_PS=$((EPSILON_PS + DELTA_PS + RECONFIG_PS))
if (( SUPERSLICE_PS <= 0 || SUPERSLICE_PS % 1000 != 0 )); then
    echo "Topology superslice duration must be a positive whole number of ns" >&2
    exit 1
fi
SUPERSLICE_NS=$((SUPERSLICE_PS / 1000))
CYCLE_NS=$((SUPERSLICE_NS * TORS))
ACTIVE_WINDOW_PS=$((EPSILON_PS + DELTA_PS))
if (( ACTIVE_WINDOW_PS <= 0 || ACTIVE_WINDOW_PS % 1000 != 0 )); then
    echo "Topology active window must be a positive whole number of ns" >&2
    exit 1
fi
ACTIVE_WINDOW_NS=$((ACTIVE_WINDOW_PS / 1000))

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
    --fanout "${FANOUT}" \
    --start-mode "${START_MODE}" \
    --start-stride "${START_STRIDE}" \
    --spread-superslices "${SPREAD_SUPERSLICES}" \
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
    echo "Credit shaping disabled; only queue overflow can reject credits"
fi

run_case() {
    local case_name="$1"
    local case_dir="${OUTPUT_ROOT}/${case_name}"
    local priority_args=()
    if [[ "${case_name}" == rxhopprio ]]; then
        priority_args=(-rxhopprio)
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
        -topfile "${GENERATED_TOPOLOGY}"
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

echo "Topology superslice: ${SUPERSLICE_NS} ns; active window: ${ACTIVE_WINDOW_NS} ns; full cycle: ${CYCLE_NS} ns"
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
