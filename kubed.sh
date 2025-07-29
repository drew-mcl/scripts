#!/usr/bin/env bash
#
# cj-debug.sh  —  Swiss-army knife for debugging Kubernetes CronJobs
#
# Features
#   • Prints logs  (-l)           kubectl logs job/<last>
#   • Clones job   (-d)           job <cronjob>-debug with sleep infinity
#   • Exec shell    (-e)          bash/sh inside the debug pod
#   • Ephemeral dbg (-p)          kubectl debug --image busybox
#   • Clean-up      (-c)          deletes the <cronjob>-debug job
#
# Requirements: kubectl ≥1.25, jq, bash 4+, sleep infinity in container image
# Usage:
#   ./cj-debug.sh -j skynet-sod            # dump logs
#   ./cj-debug.sh -j skynet-sod -d -e      # clone + shell
#   ./cj-debug.sh -j skynet-sod -p         # ephemeral shell in crashloop pod

set -euo pipefail

NAMESPACE=default
DO_LOGS=false
DO_DEBUG_JOB=false
DO_EXEC=false
DO_EPHEMERAL=false
DO_CLEAN=false
CRONJOB=""

usage() {
  cat <<EOF
Usage: $0 -j <cronjob> [options]

Options
  -j <name>    CronJob name (required)
  -n <ns>      Namespace (default: default)
  -l           Show logs of latest job
  -d           Create debug job (<cronjob>-debug) that sleeps infinity
  -e           Exec into debug job's pod (/bin/bash or /bin/sh)
  -p           Ephemeral debug shell in latest crashloop pod
  -c           Cleanup: delete <cronjob>-debug
  -h           Show this help
Examples
  $0 -j skynet-sod -l
  $0 -j skynet-sod -d -e
  $0 -j skynet-sod -p
EOF
  exit 1
}

# -------- parse opts --------------------------------------------------------
while getopts ":j:n:ldepch" opt; do
  case $opt in
    j) CRONJOB=$OPTARG ;;
    n) NAMESPACE=$OPTARG ;;
    l) DO_LOGS=true ;;
    d) DO_DEBUG_JOB=true ;;
    e) DO_EXEC=true ;;
    p) DO_EPHEMERAL=true ;;
    c) DO_CLEAN=true ;;
    h|*) usage ;;
  esac
done

[[ -z $CRONJOB ]] && { echo "❌  CronJob name (-j) is required"; usage; }

# -------- helper functions --------------------------------------------------
err()  { echo "❌  $*" >&2; exit 1; }
info() { echo "🔹 $*"; }

latest_job() {
  kubectl -n "$NAMESPACE" get jobs --for=cronjob="$CRONJOB" \
    -o json | jq -r '.items | sort_by(.metadata.creationTimestamp) | last | .metadata.name'
}

pod_for_job() {
  local job=$1
  kubectl -n "$NAMESPACE" get pods -l "job-name=$job" \
    -o jsonpath='{.items[0].metadata.name}'
}

debug_job_name="${CRONJOB}-debug"

# -------- logs --------------------------------------------------------------
if $DO_LOGS; then
  job=$(latest_job) || err "No Jobs found for CronJob $CRONJOB"
  pod=$(pod_for_job "$job") || err "No Pods found for Job $job"
  info "Logs for pod $pod (Job $job)"
  kubectl -n "$NAMESPACE" logs "$pod" || true
fi

# -------- make debug job ----------------------------------------------------
if $DO_DEBUG_JOB; then
  if kubectl -n "$NAMESPACE" get job "$debug_job_name" &>/dev/null; then
    info "Debug job $debug_job_name already exists"
  else
    info "Creating debug job $debug_job_name from CronJob $CRONJOB"
    kubectl -n "$NAMESPACE" create job --from=cronjob/"$CRONJOB" "$debug_job_name"
    # patch args -> sleep infinity
    kubectl -n "$NAMESPACE" patch job "$debug_job_name" \
      --type='json' \
      -p='[{"op":"replace","path":"/spec/template/spec/containers/0/args","value":["sleep","infinity"]}]'
  fi
fi

# -------- exec into debug job ----------------------------------------------
if $DO_EXEC; then
  pod=$(pod_for_job "$debug_job_name") || err "No debug pod found"
  info "Waiting for debug pod $pod to be Running..."
  kubectl -n "$NAMESPACE" wait --for=condition=ready pod/"$pod" --timeout=120s
  info "Opening shell inside $pod ⎈"
  kubectl -n "$NAMESPACE" exec -it "$pod" -- /bin/bash 2>/dev/null \
    || kubectl -n "$NAMESPACE" exec -it "$pod" -- /bin/sh
fi

# -------- ephemeral container ----------------------------------------------
if $DO_EPHEMERAL; then
  job=$(latest_job) || err "No Jobs found for CronJob $CRONJOB"
  pod=$(pod_for_job "$job") || err "No Pods found for Job $job"
  container=$(kubectl -n "$NAMESPACE" get pod "$pod" -o jsonpath='{.spec.containers[0].name}')
  info "Injecting busybox into $pod targeting $container"
  kubectl -n "$NAMESPACE" debug -it pod/"$pod" --image=busybox --target="$container"
fi

# -------- cleanup -----------------------------------------------------------
if $DO_CLEAN; then
  if kubectl -n "$NAMESPACE" delete job "$debug_job_name"; then
    info "Deleted debug job $debug_job_name"
  else
    err "Debug job $debug_job_name does not exist"
  fi
fi