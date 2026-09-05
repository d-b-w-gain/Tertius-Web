# ABCB Structural Software Factory Acceptance Test

## Purpose and claim boundary

This FAT gives Tertius and an independent structural assessor one controlled,
repeatable route through the evidence needed for the **ABCB Protocol for
Structural Software, Version 2011.2**. It is an assessment plan, not a declaration
that Tertius already complies.

The current product remains an **engineer-review workflow**. The words
"Complies with the ABCB Protocol for Structural Software" must not be published,
and a trained user must not sign off a job without an engineer, until:

1. all release gates in [`abcb-protocol-fat.json`](abcb-protocol-fat.json) pass;
2. the controlled Compliance Document is complete;
3. an independent structural assessor certifies the appraised scope in writing;
4. the approved release and validity period are recorded; and
5. trained-user identity, version and currency controls are operating.

The official protocol is published by the
[Australian Building Codes Board](https://ncc.abcb.gov.au/resource/protocol/protocol-structural-software).
NCC 2022 references Version 2011.2 from B1D5, H1D6 and Housing Provisions 2.2.5.

## What the appraiser receives

The assessor should not need to learn the repository before beginning. Supply a
read-only appraisal bundle with this structure:

```text
ABCB-appraisal-<release>/
  00-read-me-and-scope/
  01-compliance-document/
  02-fat-plan-and-case-register/
  03-reference-calculations/
  04-automated-test-results/
  05-witnessed-run-evidence/
  06-quality-system-and-competence/
  07-training-system/
  08-release-and-maintenance/
  09-deviations-and-retests/
  fat-run-index.json
```

`fat-run-index.json` records the release commit/image digest, environment,
operator, witness, start/end time, every FAT case result, evidence path and hash,
deviation, retest and final disposition. The assessor samples source only when a
case or discrepancy requires it.

## Roles

| Role | Responsibility |
| --- | --- |
| FAT operator | Prepares a clean release candidate, executes the written steps and captures evidence. |
| Independent witness | Observes the nominated UI and boundary cases and countersigns the run index. |
| Reference engineer | Supplies or approves independent calculation answers and tolerances without using Tertius results as the answer source. |
| Independent structural assessor | Reviews scope, methods, independence, results, deviations, QA, competence, training and the Compliance Document; issues or refuses written appraisal. |
| Release custodian | Ensures only the appraised version can display the claim or enable trained-user sign-off. |

One person may not both create a reference answer and approve Tertius's agreement
with that answer unless the assessor explicitly accepts and records that loss of
independence.

## Entry criteria

- A unique release candidate commit and deployable image digest are frozen.
- The proposed appraised scope and every exclusion are written in the Compliance
  Document.
- Reference answers and tolerances are approved before the candidate results are
  opened.
- The representative job matrix covers every appraised structural system,
  material, action region, member/connection family and geometric boundary.
- The FAT environment is isolated, reproducible and has production-equivalent
  calculation dependencies.
- Open defects and prior deviations have an explicit disposition.

Failure of an entry criterion stops the witnessed FAT; it is not recorded as a
conditional pass.

## Appraiser quick start

From the frozen source release:

```powershell
uv run pytest server/tests/structural -q --junitxml artifacts/abcb-fat/pytest.xml

uv run python scripts/verify_structural_review_pack.py `
  path/to/job-structural-review-pack.zip `
  --profile technical `
  --output artifacts/abcb-fat/review-pack-validation.json
```

The technical profile verifies the ZIP register, byte sizes, SHA-256 hashes,
report/analysis identity, controlled-draft marking, technical readiness, required
check families, selected connection sub-checks and reasoned serviceability
exclusions.

For the final release-candidate witness, run the same command with
`--profile abcb_claim`. That profile is deliberately fail-closed. It will fail
while the release disclosure says `not_appraised` / `engineer_review_required`,
or if the approved Compliance Document identity is absent. The release custodian
changes those declarations only from the written appraisal decision, never to
make the test pass in advance.

## Execution sequence

1. Record the release and environment identity in the run index.
2. Run the complete structural automated suite and archive JUnit output.
3. Run each predeclared reference job; export its PDF/review pack and verify it.
4. Compare calculation outputs and component selections with the independent
   answers using the predeclared tolerance for each quantity.
5. Witness every exact boundary and one increment beyond each boundary. Out-of-
   scope cases must produce no protocol design output.
6. Witness locked inputs, permitted overrides, invalid inputs, stale analysis,
   expired training and wrong-version training.
7. Review output identity, input register, standards register, installation
   requirements, limitations and trained-user details.
8. Review QA, author competence, training, maintenance and release-control
   records.
9. Resolve deviations and repeat every affected case plus its regression set.
10. Run the `abcb_claim` profile against a fresh final pack and hand the immutable
    evidence index to the assessor.

## Required job matrix

The matrix is owned by the Compliance Document. At minimum it includes:

- every claimed structural-system/material family and explicit unsupported type;
- each wind region and every supported terrain, shielding, topographic and
  enclosure/opening route;
- minimum, typical and maximum geometry, plus one value outside each protocol or
  product limit;
- every supported roof pitch and load direction;
- every member, stability, restraint, connection, anchor, tension and bracing
  calculation pack;
- cases where each load combination governs;
- serviceability pass/fail and reasoned non-applicability;
- missing data, contradictory data, stale analysis, tampered evidence and
  unapproved release/training states.

"The demo shed passes" is one regression case, not the validation matrix.

## Deviations and acceptance

Every case is `PASS`, `FAIL`, `BLOCKED` or `NOT RUN`. Only `PASS` satisfies a
release gate. A deviation records the requirement, observed result, safety and
scope impact, root cause, affected cases, fix commit, reviewer and retest
evidence. Conditional passes are not used for calculation, scope, output,
training or release-control requirements.

The assessor's final record must identify the exact approved release, scope,
conditions, protocol edition, Compliance Document revision, assessment method,
assessor qualifications, validity period and any triggers for reappraisal.

## Current gap register

The case register is the authoritative backlog. The present position is:

- **Implemented:** immutable/deterministic review packs; independent hash and
  evidence verifier; fail-closed required checks; removal of irrelevant partial
  connection calculators from selected evidence.
- **Partial:** calculation coverage, source traceability, report standards,
  installation assumptions and unique engine identity exist but need the full
  representative/reference matrix and protocol-specific output register.
- **Missing:** protocol-mode scope classifier and geometric lockout; locked versus
  discretionary input model; trained-user registry/currency enforcement;
  Compliance Document; formal QA/competence package; independent appraisal and
  controlled approval-state release process.

Until those gaps close, generated documents must remain controlled unsigned
drafts requiring engineer review.
