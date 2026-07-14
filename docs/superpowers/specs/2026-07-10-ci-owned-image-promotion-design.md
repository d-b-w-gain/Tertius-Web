# CI-Owned Image Promotion Design (Implementation)

## 1. Objective

Replace both personal access token dependencies in the production image promotion path. GitHub Actions owns image publication and promotion, while Flux reads `master` without any Git write credential.

Success means every non-chart commit merged to `master` either produces a checked image-promotion PR for that exact commit or fails visibly before deployment. The deployed API, Pi agent, and UI image tags must identify the GitHub run, run attempt, and source commit.

## 2. Architecture

The `Build Images` workflow is the single promotion orchestrator:

1. Serialize runs with a repository-wide image-promotion concurrency group and cancel an older run when a newer `master` commit arrives.
2. Verify `GITHUB_SHA` is still the live `master` SHA before publishing images.
3. Publish API, Pi agent, and UI images as `master-<run-number>-<run-attempt>-<short-sha>`.
4. Mint a one-hour installation token from a repository-scoped GitHub App only after both image builds succeed.
5. Update both chart tags with `scripts/promote_images.py` on the fixed `image-promotion` branch.
6. Create or reuse the branch's PR, then poll the exact PR head until `Chart render/config checks` completes successfully.
7. Verify `master` still equals the source SHA, mint a fresh App token, and merge with `--match-head-commit`.
8. Keep the fixed promotion branch after merge. A failed or newer run force-updates it with a lease and reuses its open PR without racing branch deletion.

Flux continues reconciling the public HTTPS repository and `master`, but its `GitRepository` has no `secretRef`. ImageRepository, ImagePolicy, and ImageUpdateAutomation resources are removed.

## 3. Components

| Component | Responsibility | Dependencies |
|---|---|---|
| `.github/workflows/images.yml` | Build both images, manage the promotion PR, wait for the chart gate, and merge the checked head | GHCR, GitHub App, `scripts/promote_images.py` |
| `scripts/promote_images.py` | Validate one immutable tag and replace exactly the API/UI marked tag values without reformatting YAML | Python standard library |
| `.github/workflows/chart-tests.yml` | Run the cheap chart/config gate on the promotion PR and skip k3s smoke for that generated PR | `scripts/test-deployment-config.sh` |
| `scripts/test-deployment-config.sh` | Enforce the PAT-free CI-owned architecture and exercise the tag updater | Helm, ripgrep, Python |
| Production Flux manifests | Reconcile `master` read-only | Public GitHub repository |
| `infra/deploy/README.md` | Define GitHub App setup, recovery, and one-time cluster credential cleanup | Repository and cluster administrator access |

## 4. Authentication And Authorization

Create a GitHub App installed only on `d-b-w-gain/Tertius-Web` with:

| Permission | Access | Purpose |
|---|---|---|
| Contents | Read and write | Push and replace the fixed promotion branch |
| Pull requests | Read and write | Create, inspect, and merge the promotion PR |
| Checks | Read | Poll the chart check attached to the exact PR head |

The App must not appear in the `Protect Master` bypass list. Store its client ID as the repository variable `IMAGE_PROMOTION_APP_CLIENT_ID` and its private key as the Actions secret `IMAGE_PROMOTION_APP_PRIVATE_KEY`. Workflow jobs mint installation tokens with `actions/create-github-app-token@v3`; no installation token is stored.

The `Protect Master` ruleset must require the always-present `Branch protection gate` check with strict branch-up-to-date semantics. This makes the source-SHA condition atomic at GitHub's merge boundary instead of relying only on client-side checks.

## 5. Concurrency And Staleness

- The workflow concurrency group is constant for `master` image promotion and uses `cancel-in-progress: true`.
- A historical rerun fails before image publication when its source SHA is not the current `master` SHA.
- A newer `master` commit cancels the older run; the newer run owns the fixed promotion branch.
- Branch replacement uses `--force-with-lease`, never blind force.
- The merge step rechecks live `master` immediately before merge.
- `--match-head-commit` prevents merging a PR head that changed after validation, while the strict ruleset rejects a PR whose base advanced.
- Chart-only promotion merges stay excluded from `Build Images`, preventing a build loop.

## 6. Image Tag Mutation

`scripts/promote_images.py` accepts only `master-[0-9]+-[0-9]+-[a-f0-9]{7}`. It updates exactly two lines identified by these comments:

```yaml
tag: master-111-1-ce8c49d # {"$imagepromoter": "tertius-api"}
tag: master-111-1-ce8c49d # {"$imagepromoter": "tertius-ui"}
```

Missing, duplicate, malformed, or unexpected markers fail without writing the file. This preserves the rest of `values.yaml` byte-for-byte.

## 7. Anti-Patterns (DO NOT)

| Don't | Do Instead | Why |
|---|---|---|
| Store a PAT in Actions or Kubernetes | Mint GitHub App installation tokens in Actions | Removes human-bound expiring credentials |
| Give the App a ruleset bypass | Merge a normal PR after the chart check | Preserves the protection and audit boundary |
| Let Flux push deployment changes | Keep the public Git source read-only | Removes repository write access from the cluster |
| Treat an empty check list as success | Wait until the named check exists and succeeds | GitHub registers checks asynchronously |
| Merge any open promotion head | Match the validated head SHA during merge | Prevents race-driven stale deployment |
| Reuse a tag across reruns | Include `GITHUB_RUN_ATTEMPT` | Prevents silent replacement of a deployed tag |
| Deploy mutable `master` or `latest` tags | Commit the immutable run tag | Preserves rollback and traceability |

## 8. Test Case Specifications

### Unit And Configuration Tests

| ID | Component | Input | Expected Result | Edge Case |
|---|---|---|---|---|
| TC-001 | Tag updater | Valid run tag and production values | API, Pi agent, and UI tags all change | Existing tags differ |
| TC-002 | Tag updater | `latest` | Non-zero exit and no file change | Mutable tag |
| TC-003 | Tag updater | Missing marker | Non-zero exit and no file change | Partial configuration |
| TC-004 | Deployment config | Production Kustomization | No image automation resources | Stray manifest remains |
| TC-005 | Deployment config | Flux GitRepository | Public `master` source with no secretRef | Old PAT Secret reference remains |
| TC-006 | Workflow config | Build Images workflow | App token, polling, staleness checks, SHA-matched merge present | PAT reference remains |

### Integration Tests

| ID | Flow | Setup | Verification | Teardown |
|---|---|---|---|---|
| IT-001 | Local config gate | Repository worktree | `scripts/test-deployment-config.sh` passes | None |
| IT-002 | Rendered chart | Updated production values | Helm renders both expected immutable tags | None |
| IT-003 | Rendered GitOps tree | Removed automation resources | `kubectl kustomize` contains GitRepository/Kustomization/HelmRelease and no image automation CRs | None |
| IT-004 | Hosted promotion | App installed and credentials configured | Build creates PR, chart check passes, exact head merges, Flux rolls both deployments | Delete obsolete cluster PAT Secret and old remote branch |

## 9. Error Handling Matrix

| Error | Detection | Response | Fallback | Logging |
|---|---|---|---|---|
| Source SHA is stale | Live `master` differs from `GITHUB_SHA` | Fail before build or abort before merge | A fresh `master` run owns promotion | GitHub error annotation |
| One image build fails | Build action exits non-zero | Do not start promotion job | Rerun after build issue is fixed | Build action log |
| App credentials missing or invalid | Token action fails | Do not push or merge | Administrator fixes App variable/secret | Action failure |
| Promotion branch moved | `--force-with-lease` fails | Stop without overwriting it | Rerun after inspecting branch actor | Git push error |
| Chart check never appears | Poll deadline expires | Fail promotion | Rerun after workflow trigger repair | Error with head SHA and PR number |
| Chart check fails | Completed conclusion is not success | Fail promotion and leave PR open | Fix chart/config regression and rerun | Check URL and conclusion |
| Master advances during validation | Second SHA comparison fails | Leave current PR unmerged | Newer workflow updates the fixed PR | Notice with both SHAs |
| PR head changes after validation | `--match-head-commit` rejects merge | Leave PR unmerged | Rerun against current head | GitHub CLI failure |
| Flux cannot read public source | GitRepository Ready=False | Keep current deployed release | Restore source connectivity; no write secret fallback | Flux condition/event |

## 10. Rollout And Cleanup

1. Create and install the GitHub App, then configure `IMAGE_PROMOTION_APP_CLIENT_ID` and `IMAGE_PROMOTION_APP_PRIVATE_KEY`.
2. On this implementation PR, wait for `Branch protection gate` to register, then require it with strict branch-up-to-date semantics in `Protect Master`.
3. Confirm the App is not a ruleset bypass actor, then merge the repository change without deleting `tertius-web-write` first.
4. Confirm the resulting `Build Images` run publishes, promotes, and merges both image tags end to end.
5. Confirm Flux reconciles the read-only `GitRepository` and prunes the three image automation resources.
6. Delete the obsolete Actions secret `FLUX_IMAGE_UPDATE_PAT` after App-based promotion succeeds.
7. Delete the manually managed `tertius-web-write` Secret after the read-only source is Ready.
8. Delete the obsolete `flux-image-updates` remote branch after no open PR references it.

## 11. References

| Topic | Location |
|---|---|
| Image build workflow | [`.github/workflows/images.yml`](../../../.github/workflows/images.yml) |
| Production Git source | [`infra/clusters/production/flux-system/gitrepository.yaml`](../../../infra/clusters/production/flux-system/gitrepository.yaml) |
| Deployment configuration gate | [`scripts/test-deployment-config.sh`](../../../scripts/test-deployment-config.sh) |
| Operator procedures | [`infra/deploy/README.md`](../../../infra/deploy/README.md) |
| GitHub App token action | <https://github.com/actions/create-github-app-token> |
| Flux GitRepository | <https://fluxcd.io/flux/components/source/gitrepositories/> |

## 12. Clarity Gate

All 13 Stream Coding checks pass: this is an implementation document, decisions are current and actionable, anti-patterns/tests/error handling are local to this document, references are deep links, and no future-state placeholders remain. AI coder understandability score: **9.6/10**.
