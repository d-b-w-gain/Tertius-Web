from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_pi_prompt_is_common_image_artifact_not_runtime_config() -> None:
    prompt = ROOT / "server/core/pi_agent_system_prompt.md"
    assert prompt.is_file()
    assert prompt.read_text(encoding="utf-8").startswith("Tertius file-edit policy:")
    dockerfile = read("Dockerfile.api")
    assert "COPY server/core/ ./server/core/" in dockerfile
    assert "chmod 0444 /app/server/core/pi_agent_system_prompt.md" in dockerfile
    rendered_sources = "\n".join(
        read(path)
        for path in (
            "server/.env.example",
            "infra/charts/tertius/values.yaml",
            "infra/charts/tertius/templates/pi-agent-worker.yaml",
        )
    )
    assert "PI_AGENT_SYSTEM_PROMPT" not in rendered_sources
    assert "systemPrompt:" not in rendered_sources


def test_dockerfile_has_isolated_api_and_pi_agent_targets() -> None:
    dockerfile = read("Dockerfile.api")

    assert "FROM node:24-bookworm-slim AS pi-build" in dockerfile
    assert "FROM python-app AS api" in dockerfile
    assert "FROM python-app AS pi-agent" in dockerfile
    assert "npm run install:hardened" in dockerfile
    assert "COPY server/ ./server/" not in dockerfile
    assert "server/pi/oauth-cli.ts" in dockerfile
    assert "COPY --chown=1000:1000 server/pi/workspace-guard.ts /opt/tertius-pi/workspace-guard.ts" in dockerfile
    assert "install -d -o 1000 -g 1000 /opt/tertius-pi" in dockerfile
    assert "server/pi/workspace-guard.ts /app/server/pi/" not in dockerfile
    assert "install -d -o 1000 -g 1000 -m 0700 /workspace" in dockerfile
    assert "USER 1000:1000" in dockerfile


def test_image_workflow_builds_explicit_api_and_pi_agent_targets() -> None:
    workflow = read(".github/workflows/images.yml")

    assert "target: api" in workflow
    assert "target: pi-agent" in workflow
    assert "ghcr.io/d-b-w-gain/tertius-pi-agent:${{ steps.vars.outputs.image_tag }}" in workflow
    assert "ghcr.io/d-b-w-gain/tertius-pi-agent:sha-${{ steps.vars.outputs.short_sha }}" in workflow


def test_pi_agent_image_is_tracked_by_ci_promotion() -> None:
    values = read("infra/charts/tertius/values.yaml")
    promoter = read("scripts/promote_images.py")
    ci_images = read("ci/k3s-images.txt")
    production_kustomization = read("infra/clusters/production/kustomization.yaml")

    assert not (
        ROOT / "infra/clusters/production/flux-system/image-repositories.yaml"
    ).exists()
    assert not (
        ROOT / "infra/clusters/production/flux-system/image-policies.yaml"
    ).exists()
    assert "image-repositories.yaml" not in production_kustomization
    assert "image-policies.yaml" not in production_kustomization
    assert "repository: ghcr.io/d-b-w-gain/tertius-pi-agent" in values
    assert '# {"$imagepromoter": "tertius-pi-agent"}' in values
    assert '"tertius-pi-agent"' in promoter
    assert "tertius-pi-agent:local" in ci_images


def test_ci_checks_image_identity_and_secret_isolation() -> None:
    workflow = read(".github/workflows/tests.yml")

    assert "docker build --target api" in workflow
    assert "docker build --target pi-agent" in workflow
    assert "! command -v pi" in workflow
    assert 'test "$(id -u)" = 1000' in workflow
    assert "test -z \"${DATABASE_URL:-}\"" in workflow
    assert "mkdir /workspace/image-smoke" in workflow
    assert "test \"$(stat -c %a /workspace)\" = 700" in workflow
    assert "node /app/server/pi/oauth-cli.ts invalid" in workflow
    assert '"--extension", "/opt/tertius-pi/workspace-guard.ts"' in workflow
    assert 'JSON.stringify({ id: "state", type: "get_state" })' in workflow
    assert 'JSON.stringify({ id: "commands", type: "get_commands" })' in workflow
    assert 'command.name === "tertius-workspace-guard"' in workflow


def test_docker_and_ci_npm_dependency_commands_disable_lifecycle_scripts() -> None:
    paths = [ROOT / "Dockerfile.api", ROOT / "Dockerfile.ui"]
    paths.extend((ROOT / ".github/workflows").glob("*.y*ml"))
    unsafe: list[str] = []

    for path in paths:
        content = path.read_text().replace("\\\n", " ")
        for line_number, line in enumerate(content.splitlines(), start=1):
            command = line.split("#", 1)[0]
            for match in re.finditer(
                r"\bnpm\s+(?:install|ci|prune|rebuild)\b[^;&|]*", command
            ):
                npm_command = match.group(0).strip()
                if "--ignore-scripts" not in npm_command:
                    unsafe.append(f"{path.relative_to(ROOT)}:{line_number}: {npm_command}")

    assert not unsafe, "npm dependency commands must disable lifecycle scripts:\n" + "\n".join(unsafe)
