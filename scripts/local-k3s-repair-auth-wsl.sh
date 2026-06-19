#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-tertius}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:18080}"
REALM="${REALM:-tertius}"
UI_CLIENT_ID="${UI_CLIENT_ID:-tertius-ui}"
API_CLIENT_ID="${API_CLIENT_ID:-tertius-api}"
KEYCLOAK_ADMIN_SECRET="${KEYCLOAK_ADMIN_SECRET:-tertius-keycloak-initial-admin}"

step() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf 'OK: %s\n' "$1"
}

issuer="${PUBLIC_BASE_URL}/realms/${REALM}"
jwks="http://tertius-keycloak-service:8080/realms/${REALM}/protocol/openid-connect/certs"

step "Repairing API auth environment"
kubectl -n "$NAMESPACE" set env deployment/tertius-api \
  KEYCLOAK_AUDIENCE="$API_CLIENT_ID" \
  OIDC_AUDIENCE="$API_CLIENT_ID" \
  KEYCLOAK_ISSUER="$issuer" \
  OIDC_ISSUER_URL="$issuer" \
  KEYCLOAK_JWKS_URL_OVERRIDE="$jwks" \
  >/dev/null
kubectl -n "$NAMESPACE" rollout status deployment/tertius-api --timeout=180s
ok "API auth env is set for ${issuer}"

step "Finding Keycloak pod"
keycloak_pod="$(kubectl -n "$NAMESPACE" get pod -l app=keycloak,app.kubernetes.io/managed-by=keycloak-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$keycloak_pod" ]; then
  keycloak_pod="$(kubectl -n "$NAMESPACE" get pod -l app.kubernetes.io/instance=tertius-keycloak,app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
if [ -z "$keycloak_pod" ]; then
  echo "Could not find the Keycloak server pod in namespace '${NAMESPACE}'." >&2
  kubectl -n "$NAMESPACE" get pods --show-labels >&2
  exit 1
fi
ok "Using Keycloak pod ${keycloak_pod}"

step "Reading Keycloak admin credentials"
keycloak_admin_user="$(kubectl -n "$NAMESPACE" get secret "$KEYCLOAK_ADMIN_SECRET" -o jsonpath='{.data.username}' | base64 -d)"
keycloak_admin_password="$(kubectl -n "$NAMESPACE" get secret "$KEYCLOAK_ADMIN_SECRET" -o jsonpath='{.data.password}' | base64 -d)"
if [ -z "$keycloak_admin_user" ] || [ -z "$keycloak_admin_password" ]; then
  echo "Secret '${KEYCLOAK_ADMIN_SECRET}' must contain username and password." >&2
  exit 1
fi
ok "Using admin user from secret ${KEYCLOAK_ADMIN_SECRET}"

step "Waiting for Keycloak admin endpoint"
for i in $(seq 1 60); do
  if kubectl -n "$NAMESPACE" exec "$keycloak_pod" -- /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user "$keycloak_admin_user" --password "$keycloak_admin_password" >/dev/null 2>&1; then
    ok "Keycloak admin login succeeded"
    break
  fi
  if [ "$i" = "60" ]; then
    echo "Keycloak admin endpoint did not become ready in time." >&2
    exit 1
  fi
  sleep 2
done

step "Ensuring API client '${API_CLIENT_ID}' exists"
api_client_count="$(kubectl -n "$NAMESPACE" exec "$keycloak_pod" -- /opt/keycloak/bin/kcadm.sh get clients -r "$REALM" -q clientId="$API_CLIENT_ID" --fields id --format csv --noquotes | grep -c . || true)"
if [ "$api_client_count" = "0" ]; then
  kubectl -n "$NAMESPACE" exec "$keycloak_pod" -- /opt/keycloak/bin/kcadm.sh create clients -r "$REALM" \
    -s clientId="$API_CLIENT_ID" \
    -s name="Tertius API" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s bearerOnly=true \
    -s standardFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=false >/dev/null
  ok "Created API client '${API_CLIENT_ID}'"
else
  ok "API client '${API_CLIENT_ID}' already exists"
fi

step "Finding UI client '${UI_CLIENT_ID}'"
ui_client_uuid="$(kubectl -n "$NAMESPACE" exec "$keycloak_pod" -- /opt/keycloak/bin/kcadm.sh get clients -r "$REALM" -q clientId="$UI_CLIENT_ID" --fields id --format csv --noquotes | tail -n 1)"
if [ -z "$ui_client_uuid" ]; then
  echo "Could not find UI client '${UI_CLIENT_ID}' in realm '${REALM}'." >&2
  exit 1
fi
ok "UI client id is ${ui_client_uuid}"

step "Ensuring UI access token includes audience '${API_CLIENT_ID}'"
mapper_id="$(kubectl -n "$NAMESPACE" exec "$keycloak_pod" -- /opt/keycloak/bin/kcadm.sh get "clients/${ui_client_uuid}/protocol-mappers/models" -r "$REALM" --fields id,protocolMapper --format csv --noquotes | awk -F, '$2 == "oidc-audience-mapper" { print $1; exit }')"

mapper_json="$(printf '{"%s":"%s","%s":"%s","%s":"%s","%s":false,"%s":{"%s":"%s","%s":"false","%s":"true","%s":"true","%s":"false"}}' \
  name "${API_CLIENT_ID} audience" \
  protocol openid-connect \
  protocolMapper oidc-audience-mapper \
  consentRequired \
  config \
  included.client.audience "$API_CLIENT_ID" \
  id.token.claim \
  access.token.claim \
  introspection.token.claim \
  userinfo.token.claim)"

if [ -z "$mapper_id" ]; then
  printf '%s' "$mapper_json" | base64 -w0 | kubectl -n "$NAMESPACE" exec -i "$keycloak_pod" -- /bin/sh -c "base64 -d > /tmp/tertius-audience-mapper.json && /opt/keycloak/bin/kcadm.sh create clients/${ui_client_uuid}/protocol-mappers/models -r ${REALM} -f /tmp/tertius-audience-mapper.json" >/dev/null
  ok "Created audience mapper for '${API_CLIENT_ID}'"
else
  mapper_json="$(printf '{"%s":"%s","%s":"%s","%s":"%s","%s":"%s","%s":false,"%s":{"%s":"%s","%s":"false","%s":"true","%s":"true","%s":"false"}}' \
    id "$mapper_id" \
    name "${API_CLIENT_ID} audience" \
    protocol openid-connect \
    protocolMapper oidc-audience-mapper \
    consentRequired \
    config \
    included.client.audience "$API_CLIENT_ID" \
    id.token.claim \
    access.token.claim \
    introspection.token.claim \
    userinfo.token.claim)"
  printf '%s' "$mapper_json" | base64 -w0 | kubectl -n "$NAMESPACE" exec -i "$keycloak_pod" -- /bin/sh -c "base64 -d > /tmp/tertius-audience-mapper.json && /opt/keycloak/bin/kcadm.sh update clients/${ui_client_uuid}/protocol-mappers/models/${mapper_id} -r ${REALM} -f /tmp/tertius-audience-mapper.json" >/dev/null
  ok "Updated audience mapper '${mapper_id}' to '${API_CLIENT_ID}'"
fi

step "Done"
printf 'Reload/login at: %s/\n' "$PUBLIC_BASE_URL"
