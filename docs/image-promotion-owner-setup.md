# Image Promotion Owner Setup

The owner of the `d-b-w-gain` GitHub account must complete this setup before merging [PR #296](https://github.com/d-b-w-gain/Tertius-Web/pull/296). The image-promotion workflow depends on a repository-scoped GitHub App and a strict required status check that cannot be configured by collaborators with repository write access.

## 1. Create The GitHub App

1. Sign in to GitHub as `d-b-w-gain`.
2. Open **Settings** -> **Developer settings** -> **GitHub Apps** -> **New GitHub App**.
3. Set **GitHub App name** to `Tertius Image Promotion`.
4. Set **Homepage URL** to `https://github.com/d-b-w-gain/Tertius-Web`.
5. Disable **Active** under **Webhook**. This App does not need webhook delivery.
6. Set these repository permissions:

   | Permission | Access |
   |---|---|
   | Checks | Read-only |
   | Contents | Read and write |
   | Pull requests | Read and write |

7. Under **Where can this GitHub App be installed?**, select **Only on this account**.
8. Select **Create GitHub App**.

GitHub reference: [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).

## 2. Install The App

1. Open the new App's settings.
2. Select **Install App**.
3. Select **Install** for the `d-b-w-gain` account.
4. Select **Only select repositories**.
5. Select `d-b-w-gain/Tertius-Web` and complete the installation.

GitHub reference: [Installing your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app).

## 3. Configure GitHub Actions

1. Copy the App's **Client ID** from its settings page.
2. Open `Tertius-Web` -> **Settings** -> **Secrets and variables** -> **Actions** -> **Variables**.
3. Create the repository variable:

   ```text
   IMAGE_PROMOTION_APP_CLIENT_ID
   ```

   Set its value to the App Client ID.

4. Return to the App settings and generate a private key.
5. Open `Tertius-Web` -> **Settings** -> **Secrets and variables** -> **Actions** -> **Secrets**.
6. Create the repository secret:

   ```text
   IMAGE_PROMOTION_APP_PRIVATE_KEY
   ```

   Set its value to the complete contents of the downloaded PEM file, including the `BEGIN` and `END` lines.

The workflow uses these values with `actions/create-github-app-token` to mint short-lived installation tokens. Do not store an installation token in GitHub Actions.

## 4. Update Protect Master

1. Open `Tertius-Web` -> **Settings** -> **Rules** -> **Rulesets** -> **Protect Master**.
2. Add or enable **Require status checks to pass**.
3. Require `Branch protection gate` from **GitHub Actions**.
4. Enable **Require branches to be up to date before merging**.
5. Confirm `Tertius Image Promotion` is not listed as a bypass actor.
6. Save the active ruleset.

The strict up-to-date requirement is part of the promotion's race-safety boundary. The workflow checks the live `master` SHA before merging, and the ruleset prevents `master` from advancing between that check and the server-side merge.

GitHub reference: [Managing rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository).

## 5. Confirm Readiness

Before PR #296 is marked ready and merged, confirm all of the following:

- [ ] `Tertius Image Promotion` is installed only on `d-b-w-gain/Tertius-Web`.
- [ ] The App has Checks read, Contents write, and Pull requests write permissions.
- [ ] Repository variable `IMAGE_PROMOTION_APP_CLIENT_ID` exists.
- [ ] Repository secret `IMAGE_PROMOTION_APP_PRIVATE_KEY` exists.
- [ ] `Protect Master` requires `Branch protection gate` from GitHub Actions.
- [ ] Strict branch-up-to-date enforcement is enabled.
- [ ] The App is not a ruleset bypass actor.

Do not delete `FLUX_IMAGE_UPDATE_PAT` or the cluster `tertius-web-write` Secret yet. Remove those legacy credentials only after the first App-based promotion has created, checked, and merged its image-tag PR successfully and Flux has reconciled the read-only source.

Follow [One-Time Flux Cleanup](../infra/deploy/README.md#one-time-flux-cleanup) for the readiness checks and ordered cleanup commands after that hosted promotion succeeds.
