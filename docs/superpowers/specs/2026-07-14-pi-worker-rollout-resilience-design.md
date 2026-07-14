# Pi Worker Rollout Resilience Design

## Problem

An AI follow-up published successfully to JetStream but lost both delivery attempts when KEDA terminated one Pi Job during a Flux ScaledJob update and an operator then deleted its replacement during auth verification. The worker did not NAK on cancellation, so redelivery waited for the acknowledgement lease and the job exhausted its delivery budget without producing a result.

Success means an in-flight Pi Job survives ordinary ScaledJob updates, and a worker receiving SIGTERM promptly releases its JetStream message for another Job before completing shutdown.

## Architecture

The Pi ScaledJob uses KEDA's `gradual` rollout strategy as a durability invariant. Existing Jobs continue with their original template while new Jobs use the updated template.

The worker installs a SIGTERM bridge around its one-shot `run_once` task. Cancellation first stops any in-flight result publication, promptly NAKs the request, then waits for bounded provider cleanup and propagates cancellation. Ordinary success still publishes before ACK; ordinary failures still NAK once.

The existing auth helper remains unchanged. It already requires a paused ScaledJob and refuses to proceed while any worker Job or Pod is active.

## Boundaries

- Applies only to the Pi ScaledJob; compile worker rollout behavior is unchanged.
- Does not preserve Jobs when Pi is disabled, the release is uninstalled, or the ScaledJob is deleted.
- SIGKILL, OOM, and node loss still rely on JetStream acknowledgement expiry.
- Provider calls can be repeated after redelivery; result publication and API application remain idempotent by existing message and execution identifiers.

## Anti-Patterns

| Do not | Use instead | Reason |
| --- | --- | --- |
| Increase `maxDeliver` as the primary fix | Preserve Jobs and NAK promptly | More retries only mask interruption. |
| ACK before publishing the result | Publish, then ACK | Prevents silent result loss. |
| NAK while result publication can still complete | Cancel publication before NAK | Avoids a result/redelivery race. |
| Wait for provider cleanup before NAK | NAK before bounded cleanup | Releases the lease promptly. |
| Weaken auth safety probes | Keep fail-closed active Job and Pod checks | Prevents auth operations sharing the OAuth claim with workers. |

## Test Cases

| ID | Level | Verification |
| --- | --- | --- |
| U-1 | Unit | Cancelling during provider execution NAKs exactly once and never ACKs. |
| U-2 | Unit | NAK occurs before blocked provider cleanup completes. |
| U-3 | Unit | Cancelling during result publication cancels publication, NAKs once, and never ACKs. |
| U-4 | Unit | SIGTERM cancels the one-shot worker and exits cleanly after cleanup. |
| U-5 | Unit | Existing success and publish-failure settlement tests remain green. |
| I-1 | Render | Helm output contains `spec.rollout.strategy: gradual`. |
| I-2 | Harness | Deployment configuration and runtime parity checks pass. |
| I-3 | Runtime | A k3s ScaledJob update leaves an active old-template Pi Job running. |

## Error Handling

| Failure | Behavior |
| --- | --- |
| NAK succeeds during cancellation | Propagate cancellation after provider cleanup. |
| NAK fails during cancellation | Log the failure, continue shutdown, and rely on acknowledgement expiry. |
| Result publication is in flight | Cancel and await it before NAK. |
| Provider cleanup reaches its bound | Existing Pi RPC cleanup kills the subprocess. |
| KEDA updates the ScaledJob | Keep existing Jobs; create only future Jobs from the new template. |

