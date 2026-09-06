# ABCB Structural Software FAT and Appraisal Plan

**Goal:** Make the Structural Workbench independently appraisable against the
ABCB Protocol for Structural Software without requiring the assessor to first
learn the Tertius codebase.

**Plan date:** 2026-09-05

**Status:** FAT foundation implemented; protocol product controls and external
appraisal remain open.

## FAT foundation

- [x] Create a machine-readable clause/case/evidence/release-gate register.
- [x] Create the assessor runbook, roles, entry criteria, job matrix and deviation
  process.
- [x] Add an independent review-pack verifier with technical and ABCB-claim
  profiles.
- [x] Record the current release as `not_appraised` and
  `engineer_review_required` in the manifest and evidence JSON.
- [x] Fail certificate export when a selected nested connection check is open.
- [x] Remove irrelevant partial connection calculators from selected passing
  evidence.

## Product controls required before appraisal

- [x] Add an explicit protocol-mode scope model for the appraised systems,
  materials, jurisdictions and DtS pathways.
- [x] Record and fail closed on protocol geometry limits: eaves height, highest
  roof point, width, length/width ratio and roof pitch.
- [ ] Build a complete input register with locked/discretionary/engineer-supplied
  classification and output disclosure.
- [ ] Block protocol outputs for missing, invalid, contradictory or out-of-scope
  inputs while retaining the engineer-review pathway.
- [ ] Add protocol output fields for Compliance Document identity, approved
  release status and trained-user identity/currency.
- [ ] Build the representative reference-job and negative/boundary matrix.

## Organisational and external evidence

- [ ] Approve the formal structural-software QA plan and traceability records.
- [ ] Record responsible software-author structural competence.
- [ ] Create version-controlled training, examination, certificates and currency
  rules.
- [ ] Produce the Compliance Document with worked examples and revision history.
- [ ] Execute and witness the full FAT against a frozen release candidate.
- [ ] Obtain written independent structural appraisal with exact scope and
  validity.
- [ ] Enable the public compliance claim and trained-user workflow only for the
  approved release.
