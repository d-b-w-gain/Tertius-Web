# Suggested Commands

- Follow repo command proxy convention: prefix shell commands with `rtk`.
- Frontend install/build/lint: `rtk npm install --prefix ui`, `rtk npm run build --prefix ui`, `rtk npm run lint --prefix ui`.
- API image smoke target for deployment work: `rtk docker build -f Dockerfile.api -t tertius-api:local .` then run container and curl `/` plus `/api/intus/health` when Docker is available.
- Helm deployment checks: `rtk helm dependency update charts/tertius`, `rtk helm lint charts/tertius`, `rtk helm template tertius charts/tertius`, `rtk helm template tertius charts/tertius --values charts/tertius/values-local.yaml`.
- k3s end-to-end gate when cluster prerequisites exist: `rtk scripts/test-k3s-deployment.sh`.