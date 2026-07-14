#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/pi-agent-auth.sh login  --namespace NAMESPACE --release RELEASE [--claim CLAIM] [--image IMAGE]
  scripts/pi-agent-auth.sh verify --namespace NAMESPACE --release RELEASE [--claim CLAIM] [--image IMAGE]
  scripts/pi-agent-auth.sh logout --namespace NAMESPACE --release RELEASE [--claim CLAIM] [--image IMAGE] --confirm
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

valid_dns_label() {
  local value="$1" max="$2"
  [ "${#value}" -le "$max" ] && [[ "$value" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]
}

valid_dns_subdomain() {
  local value="$1" label
  local -a labels
  [ "${#value}" -le 253 ] || return 1
  IFS='.' read -r -a labels <<<"$value"
  [ "${#labels[@]}" -gt 0 ] || return 1
  for label in "${labels[@]}"; do
    valid_dns_label "$label" 63 || return 1
  done
}

[ "$#" -gt 0 ] || { usage >&2; exit 2; }
action="$1"
shift
namespace=""
release=""
claim=""
image=""
confirmed=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --namespace) namespace="${2:-}"; shift 2 ;;
    --release) release="${2:-}"; shift 2 ;;
    --claim) claim="${2:-}"; shift 2 ;;
    --image) image="${2:-}"; shift 2 ;;
    --confirm) confirmed=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$action" in login|verify|logout) ;; *) usage >&2; exit 2 ;; esac
[ -n "$namespace" ] || die "--namespace is required"
[ -n "$release" ] || die "--release is required"
valid_dns_label "$namespace" 63 || die "--namespace must be a DNS label of at most 63 characters"
valid_dns_label "$release" 53 || die "--release must be a Helm release name of at most 53 characters"
if [ -n "$claim" ]; then
  valid_dns_subdomain "$claim" || die "--claim must be a DNS subdomain of at most 253 characters"
fi
command -v kubectl >/dev/null 2>&1 || die "kubectl is required"
command -v helm >/dev/null 2>&1 || die "helm is required"
command -v jq >/dev/null 2>&1 || die "jq is required"
values="$(helm get values "$release" --namespace "$namespace" --all -o json)"
chart_name="$(jq -r '(.nameOverride // "") | if . == "" then "tertius" else . end' <<<"$values")"
fullname="$(jq -r '.fullnameOverride // empty' <<<"$values")"
if [ -z "$fullname" ]; then
  if [[ "$release" == *"$chart_name"* ]]; then
    fullname="$release"
  else
    fullname="${release}-${chart_name}"
  fi
fi
fullname="${fullname:0:63}"
fullname="${fullname%-}"

if [ -z "$claim" ]; then
  claim="$(jq -r '.piAgent.auth.existingClaim // empty' <<<"$values")"
  if [ -z "$claim" ]; then
    claim="${fullname}-pi-agent-auth"
    claim="${claim:0:63}"
    claim="${claim%-}"
  fi
fi
[ -n "$claim" ] || die "could not resolve the Pi auth claim for release $release"
valid_dns_subdomain "$claim" || die "resolved Pi auth claim is not a valid Kubernetes DNS subdomain"

if [ -z "$image" ]; then
  repository="$(jq -r '.piAgent.image.repository // empty' <<<"$values")"
  tag="$(jq -r '.piAgent.image.tag // empty' <<<"$values")"
  [ -n "$repository" ] && [ -n "$tag" ] || die "the Helm release has no pinned Pi agent repository/tag"
  image="${repository}:${tag}"
fi

pvc_phase="$(kubectl -n "$namespace" get pvc -o jsonpath='{.status.phase}' -- "$claim" 2>/dev/null)" || \
  die "PVC $namespace/$claim does not exist"
case "$pvc_phase" in Bound|Pending) ;; *) die "PVC $namespace/$claim is $pvc_phase; expected Bound or Pending" ;; esac
app_name="$(jq -r '(.nameOverride // "") | if . == "" then "tertius" else . end' <<<"$values")"
app_name="${app_name:0:63}"
app_name="${app_name%-}"
runtime_class="$(jq -r '.piAgent.runtimeClassName // empty' <<<"$values")"
image_pull_secrets="$(jq -c '.imagePullSecrets // []' <<<"$values")"
[[ "$app_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || die "resolved chart name is not a valid Kubernetes label value"
[[ "$image" != *[[:space:]]* ]] || die "--image must not contain whitespace"

scaledjob_name="${fullname}-pi-agent"
scaledjob_name="${scaledjob_name:0:63}"
scaledjob_name="${scaledjob_name%-}"
if ! scaledjob="$(kubectl -n "$namespace" get scaledjob "$scaledjob_name" --ignore-not-found -o json 2>&1)"; then
  die "could not verify Pi ScaledJob safety: $scaledjob"
fi
if [ -n "$scaledjob" ] && ! jq -e '.metadata.annotations["autoscaling.keda.sh/paused"] == "true"' >/dev/null 2>&1 <<<"$scaledjob"; then
  die "Pi ScaledJob must be absent or annotated autoscaling.keda.sh/paused=true before auth operations"
fi

if ! worker_jobs="$(kubectl -n "$namespace" get jobs \
  -l "app.kubernetes.io/instance=${release},app.kubernetes.io/component=pi-agent-worker" -o json 2>&1)"; then
  die "could not verify active Pi worker jobs: $worker_jobs"
fi
jq -e '.items | type == "array"' >/dev/null 2>&1 <<<"$worker_jobs" || die "worker Job safety probe returned invalid data"
if ! jq -e 'all(.items[]; any(.status.conditions[]?; (.type == "Complete" or .type == "Failed") and .status == "True"))' >/dev/null <<<"$worker_jobs"; then
  die "active Pi worker jobs exist; wait for them to finish before auth operations"
fi

if ! worker_pods="$(kubectl -n "$namespace" get pods \
  -l "app.kubernetes.io/instance=${release},app.kubernetes.io/component=pi-agent-worker" \
  --field-selector=status.phase!=Succeeded,status.phase!=Failed -o json 2>&1)"; then
  die "could not verify active Pi worker pods: $worker_pods"
fi
jq -e '.items | type == "array"' >/dev/null 2>&1 <<<"$worker_pods" || die "worker pod safety probe returned invalid data"
[ "$(jq '.items | length' <<<"$worker_pods")" -eq 0 ] || die "active Pi worker pods exist; wait for them to finish before auth operations"

if [ "$action" = logout ] && [ "$confirmed" != true ]; then
  die "logout requires --confirm"
fi

pod="pi-agent-auth-${release//[^a-zA-Z0-9-]/-}-$(date +%s)-${RANDOM}"
pod="${pod,,}"
pod="${pod:0:63}"
pod="${pod%-}"
created=false
cleanup() {
  if [ "$created" = true ]; then
    kubectl -n "$namespace" delete pod --ignore-not-found --wait=false -- "$pod" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

pod_manifest="$(jq -n \
  --arg pod "$pod" --arg app "$app_name" --arg release "$release" \
  --arg image "$image" --arg claim "$claim" --arg runtimeClass "$runtime_class" \
  --argjson imagePullSecrets "$image_pull_secrets" '
  {
    apiVersion: "v1", kind: "Pod",
    metadata: {name: $pod, labels: {
      "app.kubernetes.io/name": $app,
      "app.kubernetes.io/instance": $release,
      "app.kubernetes.io/component": "pi-agent-auth-operator",
      "tertius.io/pi-agent-network": "true"
    }},
    spec: {
      automountServiceAccountToken: false, enableServiceLinks: false,
      hostIPC: false, hostNetwork: false, hostPID: false, restartPolicy: "Never",
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000,
        seccompProfile: {type: "RuntimeDefault"}},
      containers: [{
        name: "pi-agent-auth", image: $image,
        command: ["sh", "-c", "trap : TERM INT; sleep 86400 & wait"],
        env: [{name: "PI_CODING_AGENT_DIR", value: "/var/lib/pi-agent"}, {name: "HOME", value: "/tmp/home"}],
        securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}},
        resources: {requests: {cpu: "50m", memory: "128Mi"}, limits: {cpu: "500m", memory: "512Mi"}},
        volumeMounts: [{name: "auth", mountPath: "/var/lib/pi-agent"}, {name: "tmp", mountPath: "/tmp"}, {name: "home", mountPath: "/tmp/home"}]
      }],
      volumes: [{name: "auth", persistentVolumeClaim: {claimName: $claim}},
        {name: "tmp", emptyDir: {sizeLimit: "64Mi"}}, {name: "home", emptyDir: {sizeLimit: "64Mi"}}]
    }
  }
  | if $runtimeClass == "" then . else .spec.runtimeClassName = $runtimeClass end
  | if ($imagePullSecrets | length) == 0 then . else .spec.imagePullSecrets = $imagePullSecrets end
')"
created=true
printf '%s\n' "$pod_manifest" | kubectl -n "$namespace" create -f -

kubectl -n "$namespace" wait --for=jsonpath='{.status.phase}'=Bound "pvc/$claim" --timeout=180s
kubectl -n "$namespace" wait --for=condition=Ready "pod/$pod" --timeout=180s

pi_common=(--no-session --no-tools --no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-approve)
run_oauth_helper() {
  local oauth_action="$1"
  local -a exec_flags=(-i)
  if [ -t 0 ]; then
    exec_flags=(-it)
  fi
  kubectl -n "$namespace" exec "${exec_flags[@]}" "$pod" -- env PI_CODING_AGENT_DIR=/var/lib/pi-agent HOME=/tmp/home \
    node /app/server/pi/oauth-cli.ts "$oauth_action" openai-codex
}

if [ "$action" = login ]; then
  printf '%s\n' 'Complete the OpenAI Codex browser or device flow shown below.'
  run_oauth_helper login
elif [ "$action" = logout ]; then
  run_oauth_helper logout
  kubectl -n "$namespace" annotate pvc "$claim" tertius.io/pi-agent-auth-verified=false --overwrite >/dev/null
  printf '%s\n' 'Logout flow completed. The retained PVC was not deleted.'
  exit 0
fi

canary="$(kubectl -n "$namespace" exec "$pod" -- env PI_CODING_AGENT_DIR=/var/lib/pi-agent HOME=/tmp/home \
  pi "${pi_common[@]}" --provider openai-codex --model gpt-5.6 --thinking medium -p \
  'Reply with exactly PI_AUTH_OK and no other text.')"
[ "$canary" = PI_AUTH_OK ] || die "Pi OpenAI Codex verification failed"

auth_stat="$(kubectl -n "$namespace" exec "$pod" -- stat -c '%F|%u|%g|%a' /var/lib/pi-agent/auth.json)"
IFS='|' read -r file_type owner_uid owner_gid mode <<<"$auth_stat"
[ "$file_type" = "regular file" ] || die "Pi credential path is missing or is not a regular file"
[ "$owner_uid" = 1000 ] && [ "$owner_gid" = 1000 ] || die "Pi credential file must be owned by UID/GID 1000:1000"
case "$mode" in
  600|660) ;;
  *) die "Pi credential file must have mode 0600 or fsGroup-widened 0660, with no world access" ;;
esac
kubectl -n "$namespace" annotate pvc "$claim" tertius.io/pi-agent-auth-verified=true --overwrite >/dev/null
printf 'Pi OpenAI Codex authentication verified for %s/%s.\n' "$namespace" "$release"
