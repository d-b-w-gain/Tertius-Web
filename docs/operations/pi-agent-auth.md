# Pi Agent OAuth Operations

Pi uses an OpenAI Codex subscription credential stored in a retained Kubernetes
PVC. The API and UI never mount this claim. Run authentication operations only
while the Pi ScaledJob is absent, disabled, or paused at zero and no Pi worker
pod is active.

## Prerequisites

Install `kubectl`, `helm`, and `jq`, select the target cluster context, and know
the Helm namespace and release. The helper resolves the release's exact Pi image
and chart-created claim. Use `--claim` only for a replacement or externally
managed claim and `--image` only when deliberately overriding the release image.

## Provision And Verify

The claim may be `Pending` before first login when its storage class uses
`WaitForFirstConsumer`. The operator pod is the first consumer and binds it to a
node before Pi starts.

```bash
scripts/pi-agent-auth.sh login --namespace tertius --release tertius
scripts/pi-agent-auth.sh verify --namespace tertius --release tertius
```

The script runs a non-TUI helper built on Pi's public `AuthStorage.login()` API.
Complete the browser or device flow it displays. Text and manual-code prompts
are read from standard input; secret values are not echoed. The helper then
runs a no-tool `openai-codex/gpt-5.5` canary and
checks only that the credential path is a regular file owned by UID/GID
`1000:1000` with mode `0600` or `0660`. Pi writes `0600`; Kubernetes may widen
the group bits to `0660` when it applies pod `fsGroup: 1000` to the mounted PVC.
No other mode is accepted, including any world-accessible mode. The helper never
displays, encodes, or copies credential content. Its ephemeral pod is deleted on success,
failure, or interruption. A successful canary marks the PVC with a non-secret
verification annotation so local startup can identify an unprovisioned claim.
When local stdin is a terminal, the script allocates a remote TTY even if output
is redirected through `tee`, preserving masked terminal input. For piped stdin
it keeps stdin open without requesting a TTY, so a newline-delimited response
can be piped into the provider prompt.
Premature EOF fails the operation instead of waiting indefinitely.

Pi refreshes OAuth state in place. If a job reports a provider-auth failure,
pause the ScaledJob with the supported KEDA annotation, then wait for active
worker Jobs and pods to reach zero:

```bash
kubectl -n tertius annotate scaledjob tertius-pi-agent \
  autoscaling.keda.sh/paused=true --overwrite
```

Repeat login and verify. Afterward, remove the annotation only when the worker
should resume: `kubectl -n tertius annotate scaledjob tertius-pi-agent
autoscaling.keda.sh/paused-`. Do not copy the credential file or move it into a
Kubernetes Secret.

## Logout And Removal

Logout uses Pi's public `AuthStorage.logout()` API and leaves the retained claim
intact:

```bash
scripts/pi-agent-auth.sh logout --namespace tertius --release tertius --confirm
```

After the workload remains disabled and logout is complete, delete retained storage only
with an explicit operator command:

```bash
kubectl -n tertius delete pvc tertius-pi-agent-auth
```

Confirm the actual claim name before deletion. Ordinary Helm uninstall and
application backup procedures deliberately retain or exclude this OAuth state.

## Node Or Volume Loss

OAuth storage is local mutable state, not application backup data. If the node
or PV is lost, keep the worker disabled, create a replacement claim (or let a
new chart-created claim render), and run login with `--claim REPLACEMENT`. The
operator pod binds a `WaitForFirstConsumer` claim to its node. Verify before
reenabling the worker. Never restore the credential from a copied file.
