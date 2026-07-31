#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/opera_24tor_4host_55us.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"
GENERATOR="${ROOT_DIR}/run/opera_24tor_4flow_2MiB/generate_flows.py"
ANALYZER="${ROOT_DIR}/run/opera_24tor_4flow_2MiB/analyze.py"
COMPARATOR="${SCRIPT_DIR}/compare_results.py"

SEEDS=1
FLOW_MIB=2
SIMTIME=0.02
PROBE_INTERVAL=16
OUTPUT_ROOT="${SCRIPT_DIR}/results_fifo_vs_new"
BUILD=auto

usage() {
    cat <<'EOF'
Usage: bash run/opera_24tor_receiver_credit/compare.sh [options]

  --seeds LIST             Comma-separated seeds (default: 1)
  --flow-size-mib MIB      Flow size (default: 2)
  --simtime SECONDS        Simulated duration (default: 0.02)
  --probe-interval N       NEW-mode Tentative probe interval (default: 16)
  --output DIR             Result directory
  --build                  Clean-build first
  --no-build               Require an existing executable
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) SEEDS="$2"; shift 2 ;;
        --flow-size-mib) FLOW_MIB="$2"; shift 2 ;;
        --simtime) SIMTIME="$2"; shift 2 ;;
        --probe-interval) PROBE_INTERVAL="$2"; shift 2 ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if ! [[ "${SEEDS}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "Invalid seed list: ${SEEDS}" >&2
    exit 2
fi
if ! [[ "${PROBE_INTERVAL}" =~ ^[0-9]+$ ]]; then
    echo "Probe interval must be a non-negative integer" >&2
    exit 2
fi

if [[ "${BUILD}" == yes || ( "${BUILD}" == auto && ! -x "${SIMULATOR}" ) ]]; then
    make -C "${ROOT_DIR}/src/opera/datacenter" clean
    make -C "${ROOT_DIR}/src/opera" clean
    make -C "${ROOT_DIR}/src/opera"
    make -C "${ROOT_DIR}/src/opera/datacenter" htsim_xpass_dynexpTopology
elif [[ ! -x "${SIMULATOR}" ]]; then
    echo "Missing simulator: ${SIMULATOR}" >&2
    exit 1
fi

mkdir -p "${OUTPUT_ROOT}/workload"
FLOWFILE="${OUTPUT_ROOT}/workload/uniform.htsim"
python3 "${GENERATOR}" --output "${FLOWFILE}" \
    --flow-size-mib "${FLOW_MIB}" --start-superslices 8 \
    --base-start-ns 1000 --superslice-ns 55000 --active-window-ns 54000

case_args() {
    case "$1" in
        fifo)
            printf '%s\n' -rxcreditaudit
            ;;
        new)
            printf '%s\n' -rxglobaltentative -rxcredit-regular-first \
                -tentative-probe-interval "${PROBE_INTERVAL}" \
                -rxregular-hopprio -rxregular-hopweights 4 2 1 \
                -rxcreditaudit
            ;;
    esac
}

IFS=, read -r -a seed_array <<< "${SEEDS}"
for seed in "${seed_array[@]}"; do
    for mode in fifo new; do
        case_dir="${OUTPUT_ROOT}/seed_${seed}/${mode}"
        mkdir -p "${case_dir}/traffic"
        cp "${FLOWFILE}" "${case_dir}/traffic/uniform.htsim"
        extra=()
        while IFS= read -r value; do extra+=("${value}"); done < <(case_args "${mode}")
        command=("${SIMULATOR}" -flare -seed "${seed}" -simtime "${SIMTIME}"
            -utiltime 0.1 -cwnd 4 -q 600 -credq 60 -qshaping 30
            -aeolus 40 -tent 4 -winit 1.0 -tloss 0.1 -fbw 1.2
            -jita 4 -jitb 16 -fbsens "${extra[@]}" -probfile "${PROBFILE}"
            -topfile "${TOPOLOGY}" -flowfile "${case_dir}/traffic/uniform.htsim"
            -o "${case_dir}/uniform.htsim")
        printf '%q ' "${command[@]}" > "${case_dir}/command.txt"
        printf '\n' >> "${case_dir}/command.txt"
        echo "Running seed=${seed} mode=${mode}"
        "${command[@]}" > "${case_dir}/uniform.log" 2>&1
        python3 "${ANALYZER}" "${case_dir}" --simtime "${SIMTIME}" \
            --hosts-per-tor 4 --tor-count 24 --cycle-superslices 24 \
            --superslice-ns 55000
    done
done

python3 "${COMPARATOR}" --results "${OUTPUT_ROOT}" --seeds "${SEEDS}"
echo "Comparison written to ${OUTPUT_ROOT}/comparison_summary.csv"
