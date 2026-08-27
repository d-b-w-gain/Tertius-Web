# Image Promotion Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Flux image promotion and make the GitHub promotion workflow resilient to briefly stale pull-request head data.

**Architecture:** Keep the existing CI-owned promotion branch and safety checks. Extend the shell-based deployment contract test first, then minimally restore the documentation invariant, broaden workflow path coverage, and add bounded polling plus metadata refresh to the promotion job.

**Tech Stack:** GitHub Actions YAML, Bash, ripgrep, Markdown, Helm deployment contract checks.

---

### Task 1: Encode the missing CI contracts

**Files:**
- Modify: `scripts/test-deployment-config.sh`
- Test: `scripts/test-deployment-config.sh`

- [x] **Step 1: Add failing path-filter assertions**

Extract both `pull_request` and `push` triggers, then require each trigger to contain `README.md` and `ui/.env.example`:

```bash
chart_pull_request_trigger="$(extract_workflow_trigger "$CHART_WORKFLOW" pull_request)"
chart_push_trigger="$(extract_workflow_trigger "$CHART_WORKFLOW" push)"
for chart_trigger in "$chart_pull_request_trigger" "$chart_push_trigger"; do
  if ! rg -F -q -- "- 'README.md'" <<<"$chart_trigger" ||
     ! rg -F -q -- "- 'ui/.env.example'" <<<"$chart_trigger"; then
    echo ".github/workflows/chart-tests.yml must watch frontend API documentation sources." >&2
    exit 1
  fi
done
```

- [x] **Step 2: Add failing promotion-retry assertions**

Require the promotion job to refresh existing PR metadata and to contain a bounded loop that compares the reported PR head with `local_head` before continuing:

```bash
if ! rg -q '^[[:space:]]*gh pr edit([[:space:]]|$)' <<<"$promote_job" ||
   ! rg -F -q 'while [ "$SECONDS" -lt "$head_deadline" ]' <<<"$promote_job" ||
   ! rg -F -q '"${head_sha}" = "${local_head}"' <<<"$promote_job"; then
  echo "Build Images promotion must refresh reused PR metadata and wait for its pushed head." >&2
  exit 1
fi
```

- [x] **Step 3: Run the contract test and verify RED**

Run: `./scripts/test-deployment-config.sh`

Expected: FAIL because `chart-tests.yml` does not watch the documentation paths or `images.yml` lacks the bounded head-convergence loop. The existing README failure may appear first until the assertions are ordered before it.

### Task 2: Implement the minimal repair

**Files:**
- Modify: `README.md`
- Modify: `.github/workflows/chart-tests.yml`
- Modify: `.github/workflows/images.yml`
- Test: `scripts/test-deployment-config.sh`

- [x] **Step 1: Restore frontend documentation**

Add the same-origin environment setting to the frontend-only development command:

```bash
cd ui
npm install
VITE_API_URL=/api npm run dev
```

- [x] **Step 2: Trigger deployment checks for documentation changes**

Add these entries to both chart workflow path lists:

```yaml
      - 'README.md'
      - 'ui/.env.example'
```

- [x] **Step 3: Refresh reused promotion PR metadata**

In the branch where an open PR already exists, run:

```bash
gh pr edit "${pr_number}" \
  --repo "${GITHUB_REPOSITORY}" \
  --title "chore: promote ${IMAGE_TAG}" \
  --body "Promotes the API, GIS cache, Pi agent, and UI images built from ${SOURCE_SHA} as ${IMAGE_TAG}."
```

- [x] **Step 4: Poll for PR head convergence**

Replace the immediate comparison with a bounded loop:

```bash
local_head="$(git rev-parse HEAD)"
head_deadline=$((SECONDS + 60))
head_sha=""
while [ "$SECONDS" -lt "$head_deadline" ]; do
  head_sha="$(gh pr view "${pr_number}" \
    --repo "${GITHUB_REPOSITORY}" \
    --json headRefOid \
    --jq '.headRefOid')"
  if [ "${head_sha}" = "${local_head}" ]; then
    break
  fi
  echo "Waiting for promotion PR head to advance from ${head_sha} to ${local_head}."
  sleep 2
done
if [ "${head_sha}" != "${local_head}" ]; then
  echo "::error::Promotion PR head ${head_sha} does not match pushed commit ${local_head}."
  exit 1
fi
```

- [x] **Step 5: Run the contract test and verify GREEN**

Run: `./scripts/test-deployment-config.sh`

Expected: PASS, except an explicitly reported Docker-only skip when Docker is unavailable.

### Task 3: Verify and deliver through GitHub

**Files:**
- Modify: `docs/superpowers/plans/2026-08-23-image-promotion-reliability.md` checkboxes only

- [x] **Step 1: Run focused static verification**

Run: `bash -n scripts/test-deployment-config.sh`

Expected: exit 0.

Run: `ruby -e 'require "yaml"; YAML.load_file(".github/workflows/chart-tests.yml"); YAML.load_file(".github/workflows/images.yml")'`

Expected: exit 0.

- [ ] **Step 2: Run the complete deployment configuration contract**

Run: `./scripts/test-deployment-config.sh`

Expected: exit 0, with Docker-dependent coverage clearly identified if unavailable.

- [ ] **Step 3: Review the final diff**

Run: `git diff --check && git diff master...HEAD`

Expected: no whitespace errors and only the approved design, test, README, and workflow changes.

- [ ] **Step 4: Commit, push, and create the PR**

```bash
git add README.md .github/workflows/chart-tests.yml .github/workflows/images.yml scripts/test-deployment-config.sh docs/superpowers
git commit -m "fix: restore reliable image promotion"
git push -u origin codex/image-promotion-reliability
gh pr create --base master --head codex/image-promotion-reliability --title "fix: restore reliable image promotion"
```

- [ ] **Step 5: Wait for required checks and merge**

Run: `gh pr checks <number> --watch`

Expected: all required checks succeed.

Run: `gh pr merge <number> --merge --delete-branch`

Expected: PR merges into `master`.
