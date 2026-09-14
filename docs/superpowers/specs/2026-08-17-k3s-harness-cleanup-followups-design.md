# k3s Harness Cleanup Follow-ups

## 1. Goal

Close two lifecycle gaps in the disposable k3s harness while preserving its
fail-closed ownership model:

1. The external application Secret must carry the lifecycle lease in its first
   Kubernetes API write.
2. A later explicit full cleanup must be able to remove objects previously kept
   by `down --retain-data` or `down --retain-auth`.

Flux and its CRDs are assumed to be available. Flux and production-release
protection remain unchanged.

## 2. Atomic application Secret ownership

The deploy path currently applies the external application Secret and adds the
lease annotation in a second API request. An interruption between those writes
creates a Secret that the cleanup ownership checks correctly refuse to delete.

The deploy path will instead render the Secret locally, add
`tertius.io/lease-id` to that in-memory manifest, and submit the annotated
manifest with one `kubectl apply`. Secret values remain redacted from command
logging and are never written to a repository or temporary file. A failed API
write therefore leaves either no Secret or a Secret already bound to the exact
lease.

## 3. Explicit cleanup of retained tombstones

Retention cleanup continues to uninstall Helm, keep only the explicitly
selected data, and preserve a lifecycle ConfigMap with:

- `tertius.io/cleanup-policy: retain`;
- the original lease ID; and
- `tertius.io/retained-objects` entries containing kind, name, and UID.

The scheduled janitor continues to skip `retain` markers. A user-invoked plain
`down` or `delete-data` may claim a `retain` marker for full cleanup. Before the
claim, cleanup must compare every surviving retained object with the tombstone:

- a surviving object must have the same kind, name, UID, and lease ID;
- a missing recorded object is acceptable, making cleanup idempotent;
- an unrecorded release data object or an object with a replacement UID causes
  cleanup to fail before mutation.

After validation, the existing UID/resourceVersion compare-and-swap changes the
marker from `retain` to `cleaning`. Normal preconditioned deletion and absence
verification then remove the retained resources and tombstone. Janitor snapshot
claims do not gain authority over retained tombstones.

## 4. Safety and error handling

- Production release `tertius` and Flux-managed targets remain unconditionally
  protected.
- No cleanup path uses name globs or namespace deletion.
- Replacement objects are never deleted solely because they reused a name or
  lease annotation.
- Malformed retained-object metadata, UID mismatches, lease mismatches, and
  unexpected release data all fail before the marker claim or other mutation.
- Existing deletion UID/resourceVersion preconditions and final absence checks
  remain authoritative.

## 5. Validation

Test-driven regressions will prove:

1. the first server-side application Secret write already includes the lease;
2. no separate post-apply Secret annotation is required;
3. plain cleanup can remove a valid retained-data tombstone and its objects;
4. missing retained objects are tolerated;
5. replacement UIDs and unrecorded leased data are refused before mutation;
6. the janitor still skips retained tombstones; and
7. existing production, Flux, lease-mismatch, race, and absence gates remain
   green.

Required final gates are the lifecycle, janitor, process-cleanup, deployment
configuration, and runtime-parity scripts. Hosted CI remains responsible for
the production-shaped disposable k3s deploy and `always()` teardown.
