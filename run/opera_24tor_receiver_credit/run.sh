#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd -P)
SIMULATOR="${ROOT_DIR}/src/opera/datacenter/htsim_xpass_dynexpTopology"
TOPOLOGY="${ROOT_DIR}/topologies/opera_24tor_4host_55us.txt"
PROBFILE="${ROOT_DIR}/run/pfun_exp2.txt"
GENERATOR="${ROOT_DIR}/run/opera_24tor_4flow_2MiB/generate_flows.py"
ANALYZER="${ROOT_DIR}/run/opera_24tor_4flow_2MiB/analyze.py"
SUMMARIZER="${ROOT_DIR}/run/opera_24tor_4flow_2MiB/summarize_seeds.py"

SEEDS=1,2,3,4,5
MODE=all
WORKLOAD=both
OUTPUT_ROOT="${SCRIPT_DIR}/results_receiver_credit"
BUILD=auto
BEST_PROBE_INTERVAL=16
BEST_PROBE_SET=no
BEST_REGULAR_HOP=no
BEST_REGULAR_HOP_SET=no
FEEDBACK_GRACE_US=55
MEDIUM_FLOW_MIB=2
LONG_FLOW_MIB=16
MEDIUM_SIMTIME=0.02
LONG_SIMTIME=0.10

MODES=(A0 A1 B0 B1 C1 C2 C3 D1 D2 E1 E2 E3 F1)

usage() {
    cat <<'EOF'
Usage: bash run/opera_24tor_receiver_credit/run.sh [options]

  --mode MODE                 A0..F1 or all (default: all)
  --workload NAME             medium, long, or both (default: both)
  --seeds LIST                Comma-separated seeds (default: 1,2,3,4,5)
  --best-probe-interval N     Probe interval used by D/E/F (default: 16)
  --best-regular-hop          Force 4:2:1 Regular Hop WRR for E/F
  --no-best-regular-hop       Force probe-only scheduling for E/F
  --feedback-grace-us US      Grace used by E2/E3 (default: 55)
  --output DIR                Result root
  --build                     Clean-build first
  --no-build                  Require an existing executable
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --workload) WORKLOAD="$2"; shift 2 ;;
        --seeds) SEEDS="$2"; shift 2 ;;
        --best-probe-interval) BEST_PROBE_INTERVAL="$2"; BEST_PROBE_SET=yes; shift 2 ;;
        --best-regular-hop) BEST_REGULAR_HOP=yes; BEST_REGULAR_HOP_SET=yes; shift ;;
        --no-best-regular-hop) BEST_REGULAR_HOP=no; BEST_REGULAR_HOP_SET=yes; shift ;;
        --feedback-grace-us) FEEDBACK_GRACE_US="$2"; shift 2 ;;
        --output) OUTPUT_ROOT="$2"; shift 2 ;;
        --build) BUILD=yes; shift ;;
        --no-build) BUILD=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "${WORKLOAD}" in medium|long|both) ;; *) echo "Invalid workload" >&2; exit 2 ;; esac
if [[ "${MODE}" != all ]] && [[ ! " ${MODES[*]} " =~ " ${MODE} " ]]; then
    echo "Invalid mode: ${MODE}" >&2
    exit 2
fi
if ! [[ "${SEEDS}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "Invalid seed list" >&2
    exit 2
fi
if (( BEST_PROBE_INTERVAL < 0 )); then
    echo "Probe interval must be non-negative" >&2
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

case_args() {
    case "$1" in
        A0) ;;
        A1) printf '%s\n' -rxcreditaudit ;;
        B0) printf '%s\n' -rxglobaltentative -rxcreditaudit ;;
        B1) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval 0 -rxcreditaudit ;;
        C1) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval 32 -rxcreditaudit ;;
        C2) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval 16 -rxcreditaudit ;;
        C3) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval 8 -rxcreditaudit ;;
        D1) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval "${BEST_PROBE_INTERVAL}" -rxregular-hopprio -rxregular-hopweights 4 2 1 -rxcreditaudit ;;
        D2) printf '%s\n' -rxglobaltentative -rxcredit-regular-first -tentative-probe-interval "${BEST_PROBE_INTERVAL}" -rxregular-hopprio -rxregular-hopweights 8 2 1 -rxcreditaudit ;;
        E1) best_service_args; printf '%s\n' -feedback-ignore-tentative-drop -rxcreditaudit ;;
        E2) best_service_args; printf '%s\n' -feedback-regular-grace-us "${FEEDBACK_GRACE_US}" -rxcreditaudit ;;
        E3) best_service_args; printf '%s\n' -feedback-ignore-tentative-drop -feedback-regular-grace-us "${FEEDBACK_GRACE_US}" -rxcreditaudit ;;
        F1) best_service_args; printf '%s\n' -rxcredit-regular-pushout-tentative -rxcreditaudit ;;
    esac
}

best_service_args() {
    printf '%s\n' -rxglobaltentative -rxcredit-regular-first \
        -tentative-probe-interval "${BEST_PROBE_INTERVAL}"
    if [[ "${BEST_REGULAR_HOP}" == yes ]]; then
        printf '%s\n' -rxregular-hopprio -rxregular-hopweights 4 2 1
    fi
}

run_workload() {
    local workload="$1"
    local flow_mib simtime
    if [[ "${workload}" == medium ]]; then
        flow_mib="${MEDIUM_FLOW_MIB}"
        simtime="${MEDIUM_SIMTIME}"
    else
        flow_mib="${LONG_FLOW_MIB}"
        simtime="${LONG_SIMTIME}"
    fi
    local workload_root="${OUTPUT_ROOT}/${workload}"
    mkdir -p "${workload_root}/workload"
    local flowfile="${workload_root}/workload/uniform.htsim"
    python3 "${GENERATOR}" --output "${flowfile}" --flow-size-mib "${flow_mib}" \
        --start-superslices 8 --base-start-ns 1000 \
        --superslice-ns 55000 --active-window-ns 54000

    IFS=, read -r -a seed_array <<< "${SEEDS}"

    run_mode_set() {
        local modes=("$@")
        local seed mode
        for seed in "${seed_array[@]}"; do
          for mode in "${modes[@]}"; do
            local case_dir="${workload_root}/seed_${seed}/${mode}"
            mkdir -p "${case_dir}/traffic"
            cp "${flowfile}" "${case_dir}/traffic/uniform.htsim"
            local extra=()
            while IFS= read -r value; do extra+=("${value}"); done < <(case_args "${mode}")
            local command=("${SIMULATOR}" -flare -seed "${seed}" -simtime "${simtime}"
                -utiltime 0.1 -cwnd 4 -q 600 -credq 60 -qshaping 30
                -aeolus 40 -tent 4 -winit 1.0 -tloss 0.1 -fbw 1.2
                -jita 4 -jitb 16 -fbsens "${extra[@]}" -probfile "${PROBFILE}"
                -topfile "${TOPOLOGY}" -flowfile "${case_dir}/traffic/uniform.htsim"
                -o "${case_dir}/uniform.htsim")
            printf '%q ' "${command[@]}" > "${case_dir}/command.txt"
            printf '\n' >> "${case_dir}/command.txt"
            echo "Running workload=${workload} seed=${seed} mode=${mode}"
            "${command[@]}" > "${case_dir}/uniform.log" 2>&1
            python3 "${ANALYZER}" "${case_dir}" --simtime "${simtime}" \
                --hosts-per-tor 4 --tor-count 24 --cycle-superslices 24 \
                --superslice-ns 55000
            if [[ "${workload}" == long ]]; then
                python3 -c 'import csv,sys; row=next(csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8"))); ratio=float(row["flows_spanning_3_cycles_ratio"]); print(f"3-cycle flow ratio: {ratio:.2%}"); sys.exit(0 if ratio >= 0.8 else 1)' \
                    "${case_dir}/summary.csv" || {
                    echo "Long workload is too short: increase LONG_FLOW_MIB." >&2
                    exit 1
                }
            fi
          done
        done
    }

    summarize_mode_set() {
        local output="$1"
        shift
        local modes=("$@")
        local modes_csv
        modes_csv=$(IFS=,; echo "${modes[*]}")
        python3 "${SUMMARIZER}" --results "${workload_root}" \
            --seeds "${SEEDS}" --modes "${modes_csv}" --output "${output}"
    }

    local selected_modes=("${MODES[@]}")
    if [[ "${MODE}" == all ]]; then
        local phase_ac=(A0 A1 B0 B1 C1 C2 C3)
        local phase_d=(D1 D2)
        local phase_ef=(E1 E2 E3 F1)
        local phase_summary="${workload_root}/phase_a_c_summary.csv"
        local selected_probe_mode
        run_mode_set "${phase_ac[@]}"
        summarize_mode_set "${phase_summary}" "${phase_ac[@]}"
        if [[ "${BEST_PROBE_SET}" == no ]]; then
            selected_probe_mode=$(python3 -c 'import csv,sys; rows=[r for r in csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")) if r["statistic"]=="mean" and r["mode"] in {"C1","C2","C3"}]; print(max(rows, key=lambda r: float(r["total_delivered_per_used_nic_slot"]))["mode"])' "${phase_summary}")
            case "${selected_probe_mode}" in
                C1) BEST_PROBE_INTERVAL=32 ;;
                C2) BEST_PROBE_INTERVAL=16 ;;
                C3) BEST_PROBE_INTERVAL=8 ;;
                *) echo "Unable to select probe interval" >&2; exit 1 ;;
            esac
            echo "Selected probe interval ${BEST_PROBE_INTERVAL} from ${selected_probe_mode}"
        else
            case "${BEST_PROBE_INTERVAL}" in
                32) selected_probe_mode=C1 ;;
                16) selected_probe_mode=C2 ;;
                8) selected_probe_mode=C3 ;;
                *) echo "--mode all requires probe interval 32, 16, or 8" >&2; exit 1 ;;
            esac
        fi
        run_mode_set "${phase_d[@]}"
        local phase_d_summary="${workload_root}/phase_d_summary.csv"
        summarize_mode_set "${phase_d_summary}" \
            "${selected_probe_mode}" "${phase_d[@]}"
        if [[ "${BEST_REGULAR_HOP_SET}" == no ]]; then
            BEST_REGULAR_HOP=$(python3 -c 'import csv,sys; rows={(r["mode"],r["statistic"]):r for r in csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8"))}; base=rows[(sys.argv[2],"mean")]; d1=rows[("D1","mean")]; regular=float(d1["regular_delivered_per_nic_slot"]) > float(base["regular_delivered_per_nic_slot"]); total=float(d1["total_delivered_per_used_nic_slot"]) >= float(base["total_delivered_per_used_nic_slot"]); print("yes" if regular and total else "no")' "${phase_d_summary}" "${selected_probe_mode}")
            echo "Use Regular Hop WRR for E/F: ${BEST_REGULAR_HOP}"
        fi
        run_mode_set "${phase_ef[@]}"
    else
        selected_modes=("${MODE}")
        run_mode_set "${selected_modes[@]}"
    fi
    summarize_mode_set "${workload_root}/multi_seed_summary.csv" \
        "${selected_modes[@]}"
}

[[ "${WORKLOAD}" == medium || "${WORKLOAD}" == both ]] && run_workload medium
[[ "${WORKLOAD}" == long || "${WORKLOAD}" == both ]] && run_workload long
