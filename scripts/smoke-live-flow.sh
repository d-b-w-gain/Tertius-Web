#!/usr/bin/env bash
set -Eeuo pipefail

COMPILE_ONLY=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--compile-only] <ui-base-url>

Runs an authenticated live workflow through the UI origin:
  UI /api proxy -> Intus project save -> compile queue/status
  UI /api proxy -> LLM file edit job -> compile queue/status

Required environment:
  KEYCLOAK_TOKEN_URL
  KEYCLOAK_SMOKE_USERNAME (default: demo)
  KEYCLOAK_SMOKE_PASSWORD (default: demo)
  KEYCLOAK_CLIENT_ID      (default: tertius-ui)

The token request first tries the password grant for local smoke clients. When
Keycloak rejects that grant because direct access grants are disabled, it falls
back to a non-interactive authorization-code login against the same realm.

Optional environment:
  LIVE_FLOW_PROJECT
  LIVE_FLOW_MODEL_ID
  LIVE_FLOW_COMPILE_TIMEOUT_SECONDS (default: 240)
  LIVE_FLOW_AI_TIMEOUT_SECONDS      (default: 300)
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compile-only) COMPILE_ONLY=true ;;
    --help|-h) usage; exit 0 ;;
    *) break ;;
  esac
  shift
done

[ "$#" -eq 1 ] || {
  usage >&2
  exit 2
}

UI_BASE_URL="${1%/}"
API_BASE_URL="${UI_BASE_URL}/api/intus"
PROJECT_NAME="${LIVE_FLOW_PROJECT:-agent_live_flow_$(date -u +%Y%m%d%H%M%S)}"
COMPILE_TIMEOUT_SECONDS="${LIVE_FLOW_COMPILE_TIMEOUT_SECONDS:-240}"
AI_TIMEOUT_SECONDS="${LIVE_FLOW_AI_TIMEOUT_SECONDS:-300}"
TEMP_FILES=""
TOKEN=""

cleanup() {
  for file in $TEMP_FILES; do
    [ -f "$file" ] && rm -f "$file"
  done
}
trap cleanup EXIT

tmpfile() {
  file=$(mktemp "${TMPDIR:-/tmp}/tertius-live-flow.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${file}"
  printf '%s\n' "$file"
}

json_get() {
  file=$1
  expr=$2
  python3 - "$file" "$expr" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

value = data
for part in sys.argv[2].split("."):
    if not part:
        continue
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value.get(part)
    if value is None:
        break

if isinstance(value, (dict, list)):
    print(json.dumps(value))
elif value is not None:
    print(value)
PY
}

request_token_with_auth_code() {
  token_body=$1
  KEYCLOAK_TOKEN_URL="$KEYCLOAK_TOKEN_URL" \
  KEYCLOAK_CLIENT_ID="${KEYCLOAK_CLIENT_ID:-tertius-ui}" \
  KEYCLOAK_SMOKE_USERNAME="${KEYCLOAK_SMOKE_USERNAME:-demo}" \
  KEYCLOAK_SMOKE_PASSWORD="${KEYCLOAK_SMOKE_PASSWORD:-demo}" \
  python3 - "$token_body" <<'PY'
import json
import os
import sys
from http.cookies import SimpleCookie
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

token_body = sys.argv[1]
token_url = os.environ["KEYCLOAK_TOKEN_URL"]
client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "tertius-ui")
username = os.environ.get("KEYCLOAK_SMOKE_USERNAME", "demo")
password = os.environ.get("KEYCLOAK_SMOKE_PASSWORD", "demo")
redirect_uri = "http://127.0.0.1/tertius-live-flow-callback"

suffix = "/protocol/openid-connect/token"
if not token_url.endswith(suffix):
    raise SystemExit(f"KEYCLOAK_TOKEN_URL must end with {suffix}")
realm_base = token_url[: -len(suffix)]
auth_url = f"{realm_base}/protocol/openid-connect/auth?{urllib.parse.urlencode({
    'client_id': client_id,
    'redirect_uri': redirect_uri,
    'response_type': 'code',
    'scope': 'openid',
    'state': 'tertius-live-flow',
})}"


class LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._current = {"attrs": attrs, "inputs": {}}
            return
        if tag == "input" and self._current is not None:
            name = attrs.get("name")
            if name:
                self._current["inputs"][name] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(),
    NoRedirect(),
)
manual_cookies = {}


def remember_cookies(response):
    for header in response.headers.get_all("Set-Cookie", []):
        parsed = SimpleCookie()
        parsed.load(header)
        for name, morsel in parsed.items():
            manual_cookies[name] = morsel.value


def manual_cookie_header():
    return "; ".join(f"{name}={value}" for name, value in manual_cookies.items())


def open_no_redirect(request):
    try:
        return opener.open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return exc
        raise


def redirect_code(location):
    if not location:
        return ""
    parsed = urllib.parse.urlparse(location)
    values = urllib.parse.parse_qs(parsed.query)
    return (values.get("code") or [""])[0]


try:
    auth_response = open_no_redirect(urllib.request.Request(auth_url))
    remember_cookies(auth_response)
    code = redirect_code(auth_response.headers.get("Location", ""))
    if not code:
        login_html = auth_response.read().decode("utf-8", errors="replace")
        parser = LoginFormParser()
        parser.feed(login_html)
        login_form = next(
            (
                form for form in parser.forms
                if form["attrs"].get("id") == "kc-form-login"
                or "login-actions/authenticate" in form["attrs"].get("action", "")
            ),
            parser.forms[0] if parser.forms else None,
        )
        if not login_form:
            raise RuntimeError("Keycloak login form was not found")

        raw_action = urllib.parse.urljoin(auth_url, login_form["attrs"].get("action", ""))
        action_parts = urllib.parse.urlparse(raw_action)
        local_realm_parts = urllib.parse.urlparse(realm_base)
        action = urllib.parse.urlunparse((
            local_realm_parts.scheme,
            local_realm_parts.netloc,
            action_parts.path,
            action_parts.params,
            action_parts.query,
            action_parts.fragment,
        ))
        fields = dict(login_form["inputs"])
        fields["username"] = username
        fields["password"] = password
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        cookie_header = manual_cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        login_request = urllib.request.Request(
            action,
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            headers=headers,
        )
        login_response = open_no_redirect(login_request)
        remember_cookies(login_response)
        code = redirect_code(login_response.headers.get("Location", ""))
        if not code:
            body = login_response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Keycloak login did not return an authorization code: {body[:500]}")

    token_request = urllib.request.Request(
        token_url,
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token_response = opener.open(token_request, timeout=20)
    payload = token_response.read()
    parsed = json.loads(payload.decode("utf-8"))
    if not parsed.get("access_token"):
        raise RuntimeError("authorization-code token response did not include access_token")
    with open(token_body, "wb") as f:
        f.write(payload)
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    with open(token_body, "w", encoding="utf-8") as f:
        f.write(body or str(exc))
    raise SystemExit(1)
except Exception as exc:
    with open(token_body, "w", encoding="utf-8") as f:
        f.write(str(exc))
    raise SystemExit(1)
PY
}

request_token() {
  [ -n "${KEYCLOAK_TOKEN_URL:-}" ] || {
    echo "FAIL KEYCLOAK_TOKEN_URL is required for live-flow validation" >&2
    exit 1
  }
  token_body=$(tmpfile)
  status=$(curl --silent --show-error --max-time 20 \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=password" \
    -d "client_id=${KEYCLOAK_CLIENT_ID:-tertius-ui}" \
    -d "username=${KEYCLOAK_SMOKE_USERNAME:-demo}" \
    -d "password=${KEYCLOAK_SMOKE_PASSWORD:-demo}" \
    "$KEYCLOAK_TOKEN_URL" \
    -o "$token_body" \
    --write-out '%{http_code}') || {
    echo "FAIL auth token request failed" >&2
    cat "$token_body" >&2 || true
    exit 1
  }
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    if grep -q "unauthorized_client" "$token_body"; then
      if request_token_with_auth_code "$token_body"; then
        TOKEN=$(json_get "$token_body" access_token)
        [ -n "$TOKEN" ] || {
          echo "FAIL authorization-code token response did not include access_token" >&2
          cat "$token_body" >&2
          exit 1
        }
        echo "PASS auth token request"
        return
      fi
      echo "FAIL authorization-code auth token fallback failed" >&2
      cat "$token_body" >&2
      exit 1
    fi
    echo "FAIL auth token request returned HTTP ${status}" >&2
    cat "$token_body" >&2
    exit 1
  fi
  TOKEN=$(json_get "$token_body" access_token)
  [ -n "$TOKEN" ] || {
    echo "FAIL auth token response did not include access_token" >&2
    cat "$token_body" >&2
    exit 1
  }
  echo "PASS auth token request"
}

api_request() {
  method=$1
  url=$2
  body_file=${3:-}
  out=$(tmpfile)
  args=(
    --silent --show-error --max-time 60
    -H "Authorization: Bearer ${TOKEN}"
    -H "Content-Type: application/json"
    -X "$method"
    -o "$out"
    --write-out "%{http_code}"
  )
  if [ -n "$body_file" ]; then
    args+=(--data-binary "@${body_file}")
  fi
  status=$(curl "${args[@]}" "$url") || {
    echo "FAIL ${method} ${url}: curl failed" >&2
    cat "$out" >&2 || true
    exit 1
  }
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "FAIL ${method} ${url}: HTTP ${status}" >&2
    cat "$out" >&2
    exit 1
  fi
  printf '%s\n' "$out"
}

api_request_allow_exists() {
  method=$1
  url=$2
  out=$(tmpfile)
  status=$(curl --silent --show-error --max-time 60 \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -X "$method" \
    -o "$out" \
    --write-out "%{http_code}" \
    "$url") || {
    echo "FAIL ${method} ${url}: curl failed" >&2
    cat "$out" >&2 || true
    exit 1
  }
  if [ "$status" -ge 200 ] && [ "$status" -lt 300 ]; then
    printf '%s\n' "$out"
    return
  fi
  if [ "$status" = "400" ] && grep -qi "already exists" "$out"; then
    printf '%s\n' "$out"
    return
  fi
  echo "FAIL ${method} ${url}: HTTP ${status}" >&2
  cat "$out" >&2
  exit 1
}

write_json() {
  file=$1
  shift
  python3 - "$file" "$@" <<'PY'
import json
import sys

target = sys.argv[1]
kind = sys.argv[2]

if kind == "save":
    code = """import build123d as bd
box = bd.Box(10, 10, 10)
"""
    payload = {"code": code, "file": "design.py"}
elif kind == "compile":
    code = sys.argv[3]
    originating = sys.argv[4] if len(sys.argv) > 4 else ""
    payload = {
        "code": code,
        "export_format": "stl",
        "quality": "draft",
        "file": "design.py",
    }
    if originating:
        payload["originating_llm_edit_job_id"] = originating
elif kind == "llm_edit":
    files = json.loads(sys.argv[3])
    active = ""
    for item in files:
        if item.get("filename") == "design.py":
            active = item.get("id", "")
            break
    if not active and files:
        active = files[0].get("id", "")
    payload = {
        "prompt": "Add a single harmless Python comment '# live AI edit smoke' near the top of design.py. Do not change geometry.",
        "files": files[:20],
        "active_file_id": active or None,
        "metadata": {"source": "smoke-live-flow"},
    }
    model_id = sys.argv[4] if len(sys.argv) > 4 else ""
    if model_id:
        payload["model_id"] = model_id
else:
    raise SystemExit(f"unknown payload kind: {kind}")

with open(target, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY
}

ensure_project() {
  api_request_allow_exists POST "${API_BASE_URL}/projects/${PROJECT_NAME}/new" >/dev/null
  echo "PASS project available: ${PROJECT_NAME}"
}

save_seed_code() {
  request=$(tmpfile)
  write_json "$request" save
  api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/save" "$request" >/dev/null
  echo "PASS seed code saved through UI /api proxy"
}

load_design_code() {
  encoded_file="design.py"
  body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/code?file=${encoded_file}")
  json_get "$body" code
}

file_metadata_json() {
  body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/files")
  python3 - "$body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

metadata = data.get("file_metadata") or []
if not metadata:
    raise SystemExit("file metadata response is empty")
print(json.dumps(metadata))
PY
}

compile_and_wait() {
  label=$1
  originating=${2:-}
  code=$(load_design_code)
  request=$(tmpfile)
  response=$(tmpfile)
  write_json "$request" compile "$code" "$originating"
  response=$(api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/compile" "$request")
  job_id=$(json_get "$response" job_id)
  [ -n "$job_id" ] || {
    echo "FAIL ${label}: compile response did not include job_id" >&2
    cat "$response" >&2
    exit 1
  }

  deadline=$((SECONDS + COMPILE_TIMEOUT_SECONDS))
  status_body=$(tmpfile)
  while [ "$SECONDS" -lt "$deadline" ]; do
    status_body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/compile/jobs/${job_id}")
    job_status=$(json_get "$status_body" status)
    case "$job_status" in
      succeeded)
        artifact_id=$(json_get "$status_body" artifact_id)
        [ -n "$artifact_id" ] || {
          echo "FAIL ${label}: compile succeeded without artifact_id" >&2
          cat "$status_body" >&2
          exit 1
        }
        echo "PASS ${label}: compile job succeeded (${job_id})"
        return
        ;;
      failed)
        echo "FAIL ${label}: compile job failed" >&2
        cat "$status_body" >&2
        exit 1
        ;;
    esac
    sleep 3
  done
  echo "FAIL ${label}: compile job timed out" >&2
  cat "$status_body" >&2
  exit 1
}

ai_edit_and_wait() {
  metadata=$(file_metadata_json)
  request=$(tmpfile)
  response=$(tmpfile)
  write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}"
  response=$(api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs" "$request")
  job_id=$(json_get "$response" job_id)
  [ -n "$job_id" ] || {
    echo "FAIL AI edit: response did not include job_id" >&2
    cat "$response" >&2
    exit 1
  }

  deadline=$((SECONDS + AI_TIMEOUT_SECONDS))
  status_body=$(tmpfile)
  while [ "$SECONDS" -lt "$deadline" ]; do
    status_body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs/${job_id}")
    job_status=$(json_get "$status_body" status)
    case "$job_status" in
      succeeded)
        outcome=$(json_get "$status_body" result.outcome)
        [ -n "$outcome" ] || {
          echo "FAIL AI edit: completed without result outcome" >&2
          cat "$status_body" >&2
          exit 1
        }
        echo "PASS AI edit job succeeded (${job_id}, outcome=${outcome})" >&2
        printf '%s\n' "$job_id"
        return
        ;;
      failed)
        echo "FAIL AI edit job failed" >&2
        cat "$status_body" >&2
        exit 1
        ;;
    esac
    sleep 5
  done
  echo "FAIL AI edit job timed out" >&2
  cat "$status_body" >&2
  exit 1
}

request_token
ensure_project
save_seed_code
compile_and_wait "pre-edit"

if [ "$COMPILE_ONLY" = true ]; then
  echo "SKIP AI edit flow because --compile-only was requested"
  exit 0
fi

llm_job_id=$(ai_edit_and_wait)
compile_and_wait "post-AI-edit" "$llm_job_id"
echo "PASS live frontend proxy -> backend -> compile/AI edit flow"
