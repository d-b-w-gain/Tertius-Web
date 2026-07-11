# Browser Validation

Browser validation uses Chrome DevTools MCP against a local-only Chrome
debugging target. The browser profile must be isolated from personal browsing
state because MCP can inspect pages, storage, network traffic, and console logs.

## Chrome DevTools MCP Source

Use the Chrome-maintained npm package `chrome-devtools-mcp`, verified from npm
for this rollout at version `1.3.0`. The package source is published from
`https://github.com/ChromeDevTools/chrome-devtools-mcp`, which is why it is the
trusted browser harness source. Do not use `@latest` as the repo-standard
install form.

Run with privacy controls unless a human explicitly opts in:

```bash
CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS=true \
CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=true \
npx chrome-devtools-mcp@<verified-version> \
  --browser-url=http://127.0.0.1:9222 \
  --no-usage-statistics \
  --no-performance-crux
```

Replace `<verified-version>` with `1.3.0` unless a future change intentionally
updates and re-verifies the package.

Use CrUX-backed performance insights only when the validation explicitly needs
external performance data.

## Launch Chrome

```bash
mkdir -p .tmp/chrome-harness
google-chrome \
  --user-data-dir="$PWD/.tmp/chrome-harness" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --no-first-run \
  --no-default-browser-check
```

Never bind remote debugging to `0.0.0.0` or reuse a personal browser profile.
Do not keep production credentials, personal accounts, or unrelated tabs in the
harness browser.

Target URL discovery:

1. Read `.tmp/harness/k3s.env` when present.
2. Otherwise use Compose parity defaults: `http://localhost:18080`.
3. For Compose dev use `http://localhost:5173`.

Cleanup:

```bash
rm -rf .tmp/chrome-harness
```

## Canonical Journeys

Anonymous UI load:

- Preconditions: runtime is up, UI URL known.
- Navigate to `/`.
- Pass: app shell renders, no unexpected console errors, no failed core asset or
  API requests.
- Metrics: API request counters increase if `/api/auth/me` or health checks run.

Demo login:

- Preconditions: Keycloak demo user `demo / demo` exists.
- Start at the UI, use the login path, authenticate as demo.
- Pass: session returns to the app, `/api/auth/me` succeeds, no token appears in
  browser local storage.
- Metrics: auth/API request rate and failure ratio remain healthy.

Intus compile submit and status:

- Preconditions: logged in, compile worker available.
- Open Intus, submit a minimal compile, watch job status.
- Pass: job queues, finishes, and artifact metadata appears.
- Metrics: compile started/finished counters move and failure counters remain
  flat.

Generate Design live AI edit and compile:

- Preconditions: logged in, the retained Pi OAuth claim provisioned and
  verified, Pi and compile workers available.
- Run `scripts/pi-agent-auth.sh verify --namespace tertius --release
  tertius-live-flow-smoke` for the target k3s release, then run the full
  `scripts/harness-k3s.sh live-flow` before browser inspection so
  backend/proxy/auth prerequisites are proven. Compile-only mode is not valid
  evidence for this workflow.
- Open Generate Design, submit a small prompt that changes `design.py`, and
  watch the generated message and compile status.
- Pass: AI edit job reaches a terminal success state, changed file metadata is
  reflected in the UI, a post-edit compile queues and succeeds, and no prompt or
  generated source appears in telemetry/log labels.

Extus viewer load:

- Preconditions: project/artifact available.
- Open the viewer route.
- Pass: canvas is nonblank and network requests for geometry succeed.
- Failure signals: WebGL context errors, blank canvas pixels, failed artifact
  download.

Artus feature tree and AI edit entry:

- Preconditions: logged in and project available.
- Open Artus and enter the AI edit path without submitting real secrets.
- Pass: feature tree renders, edit controls are enabled, no prompt or source is
  recorded in telemetry.

Timus drafting load:

- Preconditions: drafting data or project available.
- Open Timus.
- Pass: drafting surface renders and asset requests succeed.

Artifact download:

- Preconditions: compiled artifact exists.
- Trigger download.
- Pass: response is successful and content type/size are plausible.

## Troubleshooting

- Stale port-forward: run `scripts/harness-k3s.sh status`, stop the old process,
  then rerun `scripts/harness-k3s.sh up`.
- Auth cookie mismatch: clear the harness profile or log out/in after changing
  `AUTH_COOKIE_SECURE`, issuer, or host ports.
- Keycloak issuer mismatch: verify `KEYCLOAK_ISSUER`, chart local values, and
  same-origin Keycloak nginx proxy paths.
- Blank WebGL/canvas: check console WebGL errors, canvas dimensions, and asset
  network requests; use a pixel/nonblank check for viewer changes.
- Frontend telemetry CORS failure: verify `/otel/v1/traces` in nginx or
  `http://localhost:4318/v1/traces` for Vite dev.
