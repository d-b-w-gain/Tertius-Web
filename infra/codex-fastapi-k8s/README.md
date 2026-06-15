# Codex CLI FastAPI wrapper on Kubernetes

This package builds a small FastAPI service that runs `codex exec` non-interactively and returns the final response as JSON. Runtime Codex state is stored on a PVC at `/codex-home` via `CODEX_HOME`, so `config.toml`, `auth.json`, logs, and other Codex state survive pod restarts.

## Files

- `Dockerfile` — Python + uv image, installs Codex CLI, starts FastAPI with `uv run`.
- `app/main.py` — FastAPI wrapper around `codex exec`.
- `pyproject.toml` — Python dependencies for uv.
- `k8s/codex-api.yaml` — Namespace, API key Secret, ConfigMap, PVC, Deployment, and Service.

## Build and deploy

```bash
docker build -t ghcr.io/YOUR_ORG/codex-api:0.1.0 .
docker push ghcr.io/YOUR_ORG/codex-api:0.1.0
```

Edit `k8s/codex-api.yaml` and replace:

- `ghcr.io/YOUR_ORG/codex-api:0.1.0`
- `WRAPPER_API_KEY: "replace-me"`

Then apply:

```bash
kubectl apply -f k8s/codex-api.yaml
kubectl -n codex-api rollout status deploy/codex-api
```

## Log in once

Use device auth from inside the pod:

```bash
kubectl -n codex-api exec -it deploy/codex-api -- codex login --device-auth
```

The login cache is written to `/codex-home/auth.json` on the PVC and should survive pod restarts.

If you already have a local `~/.codex/auth.json`, you can copy it into the mounted PVC instead:

```bash
POD=$(kubectl -n codex-api get pod -l app.kubernetes.io/name=codex-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n codex-api exec -i "$POD" -- sh -c 'cat > /codex-home/auth.json && chmod 600 /codex-home/auth.json' < ~/.codex/auth.json
```

## Test

```bash
kubectl -n codex-api port-forward svc/codex-api 8000:80
```

```bash
curl -sS http://localhost:8000/v1/prompt \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: replace-me' \
  -d '{"prompt":"Reply with one sentence confirming Codex is reachable."}' | jq
```

## Request shape

```json
{
  "prompt": "Explain what this repository does.",
  "model": "optional-model-name",
  "sandbox": "read-only",
  "timeout_seconds": 900
}
```

`sandbox` is limited to `read-only` or `workspace-write`. `danger-full-access` is intentionally not exposed by the API.
