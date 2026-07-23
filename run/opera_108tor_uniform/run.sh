#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/dynexp_55us_symm.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"

SIMTIME=0.05
UTILTIME=0.1
FLOW_SIZE_MIB=32
START_MODE=cycle_spread
START_STRIDE=9
BASE_START_NS=1000
CWND=40
DATA_QUEUE=600
CREDIT_QUEUE=60
SHAPING_QUEUE=30
AEOLUS_QUEUE=40
TENTATIVE_QUEUE=4
OUTPUT_DIR="${SCRIPT_DIR}/results_32MiB_cycle_spread_55us"
BUILD=auto
SHAPING_ENABLED=yes
RX_HOP_PRIO=no

usage() {
    cat <<'EOF'
Usage: bash run/opera_108tor_uniform/run.sh [options]

Options:
  --flow-size-mib MIB       Size of each of the 648 flows (default: 32)
  --start-mode MODE         cycle_spread or synchronized (default: cycle_spread)
  --start-stride SLICES     Per-lane cycle-spread stride (default: 9)
  --base-start-ns NS        Earliest flow start time (default: 1000)
  --simtime SECONDS         Simulation duration (default: 0.05)
  --utiltime MS             Utilization sampling interval (default: 0.1)
  --cwnd PACKETS            Initial unscheduled window (default: 40)
  --queue PACKETS           Data queue capacity (default: 600)
  --credq PACKETS           Credit queue capacity (default: 60)
  --qshaping PACKETS        Probabilistic shaping threshold (default: 30)
  --aeolus PACKETS          Unscheduled-data allowance (default: 40)
  --tent PACKETS            Tentative credit threshold (default: 4)
  --probfile FILE           Credit hop-probability file
  --topology FILE           Opera topology (default: dynexp_55us_symm.txt)
  --no-shaping              Disable probabilistic admission shaping
  --rxhopprio               Prioritize current shortest Credit path at receiver NIC
  --output DIR              Result directory
  --build                   Clean-build the dynamic Opera executable
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
        --start-stride) START_STRIDE="$2"; shift 2 ;;
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
        --rxhopprio) RX_HOP_PRIO=yes; shift ;;
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

read -r HOSTS HOSTS_PER_TOR UPLINKS TORS < <(sed -n '1p' "${TOPOLOGY}")
read -r TOPOLOGY_SLICES EPSILON_PS DELTA_PS RECONFIG_PS < <(sed -n '2p' "${TOPOLOGY}")
if [[ "${HOSTS}" -ne 648 || "${HOSTS_PER_TOR}" -ne 6 || \
      "${UPLINKS}" -ne 6 || "${TORS}" -ne 108 ]]; then
    echo "This workload requires a 648-host, 108-ToR, 6+6 topology" >&2
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

mkdir -p "${OUTPUT_DIR}/traffic"
FLOWFILE="${OUTPUT_DIR}/traffic/uniform.htsim"
STDOUT_LOG="${OUTPUT_DIR}/uniform.log"
HTSIM_LOG="${OUTPUT_DIR}/uniform.htsim"

python3 "${SCRIPT_DIR}/generate_flows.py" \
    --output "${FLOWFILE}" \
    --flow-size-mib "${FLOW_SIZE_MIB}" \
    --start-mode "${START_MODE}" \
    --start-stride "${START_STRIDE}" \
    --base-start-ns "${BASE_START_NS}" \
    --superslice-ns "${SUPERSLICE_NS}"

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

RX_HOP_PRIO_ARGS=()
if [[ "${RX_HOP_PRIO}" == yes ]]; then
    RX_HOP_PRIO_ARGS=(-rxhopprio)
    echo "Receiver current-path Credit priority enabled"
fi

COMMAND=(
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
    "${RX_HOP_PRIO_ARGS[@]}"
    "${PROB_ARGS[@]}"
    -topfile "${TOPOLOGY}"
    -flowfile "${FLOWFILE}"
    -o "${HTSIM_LOG}"
)

printf '%q ' "${COMMAND[@]}" > "${OUTPUT_DIR}/command.txt"
printf '\n' >> "${OUTPUT_DIR}/command.txt"
echo "Topology superslice: ${SUPERSLICE_NS} ns; full cycle: ${CYCLE_NS} ns"
echo "Running ${HOSTS} balanced flows for ${SIMTIME}s..."
if "${COMMAND[@]}" > "${STDOUT_LOG}" 2>&1; then
    echo "Simulation completed."
else
    status=$?
    echo "Simulation failed with exit code ${status}. Last 80 log lines:" >&2
    tail -n 80 "${STDOUT_LOG}" >&2
    exit "${status}"
fi

python3 "${SCRIPT_DIR}/analyze.py" "${OUTPUT_DIR}" \
    --simtime "${SIMTIME}" \
    --hosts-per-tor "${HOSTS_PER_TOR}" \
    --tor-count "${TORS}" \
    --cycle-superslices "${TORS}" \
    --superslice-ns "${SUPERSLICE_NS}"
