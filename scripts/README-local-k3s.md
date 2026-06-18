# Local k3s Helper Scripts

These scripts are the fast path for running and patching the local k3s dev stack
at `http://localhost:18080/`. They are intentionally small wrappers around the
same chart/images used by the cloud deployment, so local fixes stay close to
production behavior.

## Start or Recover Local Dev

```powershell
.\scripts\local-k3s-start.cmd
```

Use this after rebooting, WSL restarting, or when the local stack has stale pods.
It starts k3s if needed, waits for the Tertius pods, repairs the local Keycloak
issuer/audience settings, starts the `localhost:18080` tunnel, and patches the UI
bundle from the current checkout.

## Patch Frontend Changes

```powershell
.\scripts\local-k3s-patch-ui.cmd
```

Use this after editing `ui/`. It builds the Vite bundle with local `/api` and
Keycloak settings, copies `ui/dist` into the running UI pod, and verifies
`http://localhost:18080/` returns HTTP 200.

## Patch API Changes

```powershell
.\scripts\local-k3s-patch-api.cmd
```

Use this after editing backend API code under `server/`, including Timus API
logic. It builds a fresh `localhost/tertius-api:local-<timestamp>` image, imports
it into k3s containerd, updates the `tertius-api` deployment, waits for rollout,
and verifies `http://localhost:18080/api/`.

Note: this patches the long-running API deployment only. It warns instead of
patching the KEDA compile `ScaledJob`; run a full local redeploy/start flow when
you need to test compile-worker code changes.

## Repair Auth Only

```powershell
.\scripts\local-k3s-repair-auth.cmd
```

Use this when pods are healthy but login/API calls fail with stale issuer,
audience, or bearer-token behavior. You may still need to log out and back in
from the browser after repairing auth.

## Troubleshooting

- `502 Bad Gateway` usually means the UI tunnel is up but the API pod/service is
  not ready. Run `.\scripts\local-k3s-start.cmd`.
- If an API patch fails mid-way, rerun `.\scripts\local-k3s-start.cmd` to recover
  the tunnel and pod readiness before trying again.
- Docker must be available either from Windows or as root inside
  `Ubuntu-24.04` WSL for `local-k3s-patch-api.cmd`.
