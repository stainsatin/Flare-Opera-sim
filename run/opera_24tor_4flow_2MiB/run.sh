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
MODE=all
SEEDS=1,2,3,4,5
CWND=4
DATA_QUEUE=600
CREDIT_QUEUE=60
SHAPING_QUEUE=30
AEOLUS_QUEUE=40
TENTATIVE_QUEUE=4
OUTPUT_ROOT="${SCRIPT_DIR}/results_rcdcp_4x2MiB_stagger8"
BUILD=auto
SLOT_TRACE=yes

usage() {
    cat <<'EOF'
Usage: bash run/opera_24tor_4flow_2MiB/run.sh [options]

Options:
  --mode MODE               fifo_original, fifo_global, wrr421, wrr821, or all
  --seeds LIST              Comma-separated seeds (default: 1,2,3,4,5)
  --flow-size-mib MIB       Size of every flow (default: 2)
  --start-superslices N     Release over 4, 8, or 16 superslices (default: 8)
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
  --flow-generator FILE     Compatible workload generator
  --topology FILE           Compatible 96-host, 24-ToR Opera topology
  --no-slot-trace           Skip event-level NIC/admission trace lines
  --output DIR              Root directory for all seed/mode results
  --build                   Clean-build the dynamic Opera executable
  --no-build                Require an existing executable
  -h, --help                Show this help

RCDCP push-out is deliberately off. Enable -rxcreditpushout only in a separate
manual experiment so admission and service effects remain attributable.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            case "$2" in
                fifo_original|fifo_global|wrr421|wrr821|all) MODE="$2" ;;
                *) echo "Invalid --mode: $2" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --seeds) SEEDS="$2"; shift 2 ;;
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
        --no-slot-trace) SLOT_TRACE=no; shift ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -f "${TOPOLOGY}" || ! -f "${FLOW_GENERATOR}" || ! -f "${PROBFILE}" ]]; then
    echo "Missing topology, flow generator, or probability file" >&2
    exit 1
fi
case "${START_SUPERSLICES}" in 4|8|16) ;; *) echo "Invalid start span" >&2; exit 2 ;; esac
if ! [[ "${SEEDS}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "--seeds must be comma-separated non-negative integers" >&2
    exit 2
fi

read -r HOSTS HOSTS_PER_TOR UPLINKS TORS < <(sed -n '1p' "${TOPOLOGY}")
read -r TOPOLOGY_SLICES EPSILON_PS DELTA_PS RECONFIG_PS < <(sed -n '2p' "${TOPOLOGY}")
if [[ "${HOSTS}" -ne 96 || "${HOSTS_PER_TOR}" -ne 4 || \
      "${UPLINKS}" -ne 6 || "${TORS}" -ne 24 || \
      "${TOPOLOGY_SLICES}" -ne 72 ]]; then
    echo "Expected the 96-host, 24-ToR, 4+6 Opera topology" >&2
    exit 1
fi
SUPERSLICE_PS=$((EPSILON_PS + DELTA_PS + RECONFIG_PS))
ACTIVE_WINDOW_PS=$((EPSILON_PS + DELTA_PS))
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

case_args() {
    case "$1" in
        fifo_original) ;;
        fifo_global) printf '%s\n' -rxglobaltentative ;;
        wrr421) printf '%s\n' -rxglobaltentative -rxhopprio -rxhopweights 4 2 1 ;;
        wrr821) printf '%s\n' -rxglobaltentative -rxhopprio -rxhopweights 8 2 1 ;;
    esac
}

run_case() {
    local seed="$1"
    local case_name="$2"
    local case_dir="${OUTPUT_ROOT}/seed_${seed}/${case_name}"
    local priority_args=()
    while IFS= read -r arg; do priority_args+=("${arg}"); done < <(case_args "${case_name}")
    local trace_args=()
    [[ "${SLOT_TRACE}" == yes ]] && trace_args=(-rxcreditslottrace)

    mkdir -p "${case_dir}/traffic"
    cp "${SHARED_FLOWFILE}" "${case_dir}/traffic/uniform.htsim"
    local command=(
        "${SIMULATOR}" -flare -seed "${seed}"
        -simtime "${SIMTIME}" -utiltime "${UTILTIME}"
        -cwnd "${CWND}" -q "${DATA_QUEUE}" -credq "${CREDIT_QUEUE}"
        -qshaping "${SHAPING_QUEUE}" -aeolus "${AEOLUS_QUEUE}"
        -tent "${TENTATIVE_QUEUE}" -winit 1.0 -tloss 0.1 -fbw 1.2
        -jita 4 -jitb 16 -fbsens
        "${priority_args[@]}" "${trace_args[@]}"
        -probfile "${PROBFILE}" -topfile "${TOPOLOGY}"
        -flowfile "${case_dir}/traffic/uniform.htsim"
        -o "${case_dir}/uniform.htsim"
    )
    printf '%q ' "${command[@]}" > "${case_dir}/command.txt"
    printf '\n' >> "${case_dir}/command.txt"
    echo "Running seed=${seed} mode=${case_name}: ${FLOW_COUNT} flows for ${SIMTIME}s..."
    "${command[@]}" > "${case_dir}/uniform.log" 2>&1
    python3 "${SCRIPT_DIR}/analyze.py" "${case_dir}" \
        --simtime "${SIMTIME}" --hosts-per-tor "${HOSTS_PER_TOR}" \
        --tor-count "${TORS}" --cycle-superslices "${TORS}" \
        --superslice-ns "${SUPERSLICE_NS}"
}

if [[ "${MODE}" == all ]]; then
    MODES=(fifo_original fifo_global wrr421 wrr821)
else
    MODES=("${MODE}")
fi
IFS=, read -r -a SEED_ARRAY <<< "${SEEDS}"

echo "Topology cycle: ${CYCLE_NS} ns; simtime covers $(awk -v s="${SIMTIME}" -v c="${CYCLE_NS}" 'BEGIN {print s*1e9/c}') cycles"
echo "Workload: ${FLOW_COUNT} flows x ${FLOW_SIZE_MIB} MiB; push-out=off"
for seed in "${SEED_ARRAY[@]}"; do
    for case_name in "${MODES[@]}"; do
        run_case "${seed}" "${case_name}"
    done
done

python3 "${SCRIPT_DIR}/summarize_seeds.py" \
    --results "${OUTPUT_ROOT}" --seeds "${SEEDS}" \
    --modes "$(IFS=,; echo "${MODES[*]}")" \
    --output "${OUTPUT_ROOT}/multi_seed_summary.csv"
echo "Wrote ${OUTPUT_ROOT}/multi_seed_summary.csv"
