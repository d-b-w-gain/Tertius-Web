# Task Completion

- For deployment work, verify: frontend build, API/UI Docker image build when Docker is available, `helm dependency update`, `helm lint`, and `helm template` for default and local values.
- Definition of done for the Kubernetes deployment plan is the k3s harness passing against a local k3s cluster: `scripts/test-k3s-deployment.sh`.
- If k3s prerequisites are missing, record exact failing prerequisite command and do not claim completion.