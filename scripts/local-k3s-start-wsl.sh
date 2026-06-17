#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-tertius}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
SERVICE_PORT="${SERVICE_PORT:-80}"

step() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf 'OK: %s\n' "$1"
}

warn() {
  printf 'WARN: %s\n' "$1"
}

wait_for_kubectl() {
  local attempts="${1:-60}"
  local delay="${2:-3}"
  local i

  for i in $(seq 1 "$attempts"); do
    if kubectl get nodes >/dev/null 2>&1; then
      return 0
    fi
    warn "Kubernetes API is not ready yet ($i/$attempts). Waiting ${delay}s."
    sleep "$delay"
  done

  return 1
}

get_unready_controller_pods() {
  kubectl -n "$NAMESPACE" get pods \
    -l 'app.kubernetes.io/component in (api,ui),app.kubernetes.io/instance=tertius' \
    --no-headers 2>/dev/null |
    awk '
      {
        split($2, ready, "/")
        if ($3 != "Running" || ready[1] != ready[2]) {
          print $1
        }
      }
    '
  kubectl -n "$NAMESPACE" get pods \
    -l 'app.kubernetes.io/name in (valkey,nats)' \
    --no-headers 2>/dev/null |
    awk '
      {
        split($2, ready, "/")
        if ($3 != "Running" || ready[1] != ready[2]) {
          print $1
        }
      }
    '
  kubectl -n "$NAMESPACE" get pods \
    -l 'cnpg.io/cluster' \
    --no-headers 2>/dev/null |
    awk '
      {
        split($2, ready, "/")
        if ($3 != "Running" || ready[1] != ready[2]) {
          print $1
        }
      }
    '
  kubectl -n "$NAMESPACE" get pods \
    -l 'app=keycloak,app.kubernetes.io/managed-by=keycloak-operator' \
    --no-headers 2>/dev/null |
    awk '
      {
        split($2, ready, "/")
        if ($3 != "Running" || ready[1] != ready[2]) {
          print $1
        }
      }
    '
  kubectl -n "$NAMESPACE" get pods \
    -l 'app.kubernetes.io/name=keycloak-operator' \
    --no-headers 2>/dev/null |
    awk '
      {
        split($2, ready, "/")
        if ($3 != "Running" || ready[1] != ready[2]) {
          print $1
        }
      }
    '
}

bad_pod_count() {
  get_unready_controller_pods | sed '/^$/d' | wc -l
}

recycle_unready_controller_pods() {
  local pods
  pods="$(get_unready_controller_pods | sed '/^$/d' | sort -u)"
  if [ -z "$pods" ]; then
    return 0
  fi

  warn "Recycling unready long-lived pods after k3s runtime restart:"
  printf '%s\n' "$pods"
  printf '%s\n' "$pods" | xargs -r kubectl -n "$NAMESPACE" delete pod --wait=false
}

wait_for_pods() {
  local attempts="${1:-60}"
  local delay="${2:-5}"
  local i
  local bad

  for i in $(seq 1 "$attempts"); do
    wait_for_kubectl 20 3
    if [ "$(bad_pod_count)" = "0" ]; then
      return 0
    fi
    warn "Pods are still settling ($i/$attempts). Waiting ${delay}s."
    kubectl -n "$NAMESPACE" get pods || true
    sleep "$delay"
  done

  return 1
}

step "Checking k3s service"
if ! systemctl is-active k3s >/dev/null 2>&1; then
  warn "k3s is not active. Starting it."
  systemctl start k3s
else
  ok "k3s service is active"
fi

step "Waiting for Kubernetes API from inside WSL"
if ! wait_for_kubectl 60 3; then
  warn "Kubernetes API did not answer. Restarting k3s once."
  systemctl restart k3s
  wait_for_kubectl 60 3
fi

step "Kubernetes node"
kubectl get nodes

step "Current Tertius pods"
kubectl -n "$NAMESPACE" get pods

if [ "$(bad_pod_count)" != "0" ]; then
  warn "Pods are not ready yet. Restarting k3s once, then waiting without rolling every workload."
  systemctl restart k3s
  wait_for_kubectl 60 3
  recycle_unready_controller_pods
  wait_for_pods 72 5 || {
    warn "Pods did not all become ready. Current state:"
    kubectl -n "$NAMESPACE" get pods || true
    exit 1
  }
fi

step "Ready Tertius pods"
kubectl -n "$NAMESPACE" get pods

step "Setting local auth issuer"
PUBLIC_BASE_URL=http://localhost:18080 NAMESPACE="$NAMESPACE" bash ./scripts/local-k3s-repair-auth-wsl.sh

step "Starting localhost:18080 tunnel from inside WSL"
pkill -f 'kubectl -n tertius port-forward pod/.*18080:80' >/dev/null 2>&1 || true
ui_pod="$(kubectl -n "$NAMESPACE" get pod -l app.kubernetes.io/component=ui -o jsonpath='{.items[0].metadata.name}')"

for i in $(seq 1 30); do
  if kubectl -n "$NAMESPACE" exec "$ui_pod" -- sh -lc 'wget -q -O- http://127.0.0.1:80/ >/dev/null' >/dev/null 2>&1; then
    break
  fi
  warn "UI pod is ready but nginx is not answering yet ($i/30). Waiting 2s."
  sleep 2
done

nohup kubectl -n "$NAMESPACE" port-forward "pod/${ui_pod}" "18080:80" --address 0.0.0.0 > /tmp/tertius-ui-18080.log 2>&1 &
sleep 3

if command -v curl >/dev/null 2>&1; then
  tunnel_ok=false
  for i in $(seq 1 10); do
    if curl -fsS "http://127.0.0.1:18080/api/" >/dev/null 2>&1; then
      tunnel_ok=true
      break
    fi
    sleep 1
  done

  if [ "$tunnel_ok" != "true" ]; then
    warn "Tunnel started but API health did not answer through it. Log follows:"
    cat /tmp/tertius-ui-18080.log || true
    exit 1
  fi
else
  if ! ss -ltnp | grep -q ':18080'; then
    warn "Tunnel did not appear to listen on 18080. Log follows:"
    cat /tmp/tertius-ui-18080.log || true
    exit 1
  fi
fi

step "Final local URLs"
printf 'Frontend: http://localhost:18080/\n'
printf 'API health: http://localhost:18080/api/\n'
printf 'Artus health: http://localhost:18080/api/artus/health\n'
printf '\nIf projects fail with Invalid bearer token, the browser is probably holding an old token.\n'
printf 'Open this logout URL, then log in again:\n'
printf 'http://localhost:18080/realms/tertius/protocol/openid-connect/logout?client_id=tertius-ui&post_logout_redirect_uri=http%%3A%%2F%%2Flocalhost%%3A18080%%2F\n'
