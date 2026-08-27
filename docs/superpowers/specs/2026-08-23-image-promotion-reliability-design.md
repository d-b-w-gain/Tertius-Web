# Image Promotion Reliability Design

## Goal

Restore automated promotion of immutable application image tags into the Helm
chart consumed by Flux, and prevent the same failures from recurring.

## Root Causes

The deployment configuration test requires both `README.md` and
`ui/.env.example` to document `VITE_API_URL=/api`. The README refresh removed
that literal, but the deployment-chart workflow did not watch `README.md`, so
the regression reached `master`. The next image-promotion PR changed the chart,
triggered the deployment test, and inherited the failure.

Separately, the image workflow force-pushes a reused `image-promotion` branch
and immediately reads the pull request head through GitHub's API. GitHub can
briefly return the previous head after the ref update, causing a false stale-head
failure before the chart check is evaluated.

## Design

1. Add the same-origin frontend setting to the README's frontend development
   instructions using the exact validated form `VITE_API_URL=/api`.
2. Add `README.md` and `ui/.env.example` to both pull-request and `master` push
   path filters in the deployment-chart workflow. Either source of the
   documentation invariant will then run the check that enforces it.
3. After pushing the promotion branch, retain the locally known commit SHA as
   the expected head and poll the pull request until its reported head matches.
   Use a short bounded timeout and fail with both SHAs if convergence does not
   occur. Subsequent check and merge safeguards remain unchanged.
4. When reusing an open promotion PR, refresh its title and body to describe the
   newly staged image tag and source commit.

## Error Handling

The head polling loop succeeds only on an exact SHA match. It retries stale
values at a fixed short interval and exits nonzero after a bounded deadline.
This tolerates API propagation delay without accepting an unexpected branch
state. The existing pre-merge master and PR-head checks continue to prevent
stale deployment.

## Testing

Extend the deployment configuration contract test before modifying production
workflow files. The test will require:

- both documentation paths in the chart-test trigger sections;
- the README's same-origin API setting;
- bounded retry logic around promotion PR head convergence; and
- promotion PR metadata refresh when an existing PR is reused.

Run the focused deployment configuration test, promotion-script tests, workflow
syntax checks, and the repository's appropriate deployment validation. Full k3s
validation is unnecessary because this change affects CI orchestration and
documentation rather than rendered runtime behavior.
