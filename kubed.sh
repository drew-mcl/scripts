#!/usr/bin/env bash
# cj-debug.sh ─ debug a CronJob that exits immediately with no logs
# deps: kubectl, jq

set -euo pipefail

NS=default
CJ=""
ACTION=logs         # logs | clone | exec | eph | clean

while getopts ":j:n:a:h" o; do
  case $o in
    j) CJ=$OPTARG ;;
    n) NS=$OPTARG ;;
    a) ACTION=$OPTARG ;;           # pick one action
    h|*) echo "Usage: $0 -j <cronjob> [-n <ns>] -a <logs|clone|exec|eph|clean>"; exit 1 ;;
  esac
done
[[ -z $CJ ]] && { echo "CronJob (-j) required"; exit 1; }

debug_job=${CJ}-debug

# ---- helpers ---------------------------------------------------------------
latest_job() {
  kubectl -n "$NS" get jobs -l "cronjob.kubernetes.io/instance=$CJ" \
    -o json | jq -r '.items|sort_by(.metadata.creationTimestamp)|last|.metadata.name'
}

pod_for_job() {
  kubectl -n "$NS" get pods -l "job-name=$1" \
    -o jsonpath='{.items[0].metadata.name}'
}

# ---- actions ---------------------------------------------------------------
case $ACTION in
  logs)
    job=$(latest_job) || { echo "No Job yet"; exit 1; }
    pod=$(pod_for_job "$job") || { echo "No Pod yet"; exit 1; }
    kubectl -n "$NS" logs "$pod" --all-containers --timestamps || true
    ;;

  clone)
    if kubectl -n "$NS" get job "$debug_job" &>/dev/null; then
      echo "Debug Job already exists"; exit 0
    fi
    kubectl -n "$NS" create job --from=cronjob/"$CJ" "$debug_job" \
      -- /bin/sh -c 'sleep infinity'
    echo "Created $debug_job.  Use -a exec to shell in."
    ;;

  exec)
    pod=$(pod_for_job "$debug_job") || { echo "Debug Job not running"; exit 1; }
    kubectl -n "$NS" exec -it "$pod" -- /bin/sh
    ;;

  eph)
    job=$(latest_job) || { echo "No failing Job"; exit 1; }
    pod=$(pod_for_job "$job") || { echo "No Pod yet"; exit 1; }
    main=$(kubectl -n "$NS" get pod "$pod" -o jsonpath='{.spec.containers[0].name}')
    kubectl -n "$NS" debug -it pod/"$pod" --image=busybox --target="$main"
    ;;

  clean)
    kubectl -n "$NS" delete job "$debug_job" --ignore-not-found
    ;;

  *) echo "Unknown action $ACTION"; exit 1 ;;
esac