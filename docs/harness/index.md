# Harness Validation

The harness docs define how to validate Tertius changes in a production-shaped
local environment without loading every runtime detail into an agent prompt.
Procedural Codex behavior lives in the repo-owned skill under
`tools/codex/skills/tertius-harness`; durable runtime facts live here.

## Which Runtime Should I Use?

| Work | Runtime | Entry point |
| --- | --- | --- |
| Fast React/Python iteration | Compose dev | `scripts/harness-compose.sh dev-up` |
| Full-stack agent validation | k3s | `scripts/harness-k3s.sh up` |
| Existing k3s release validation | k3s port-forwards | `scripts/harness-k3s.sh ports` |
| Image/nginx sanity without k3s | Compose parity | `scripts/harness-compose.sh parity-up` |
| CI deploy smoke | GitHub k3s workflow | `.github/workflows/chart-tests.yml` |
| Live authenticated compile/AI edit flow | active k3s or Compose parity runtime | `scripts/harness-k3s.sh live-flow` or `scripts/harness-compose.sh live-flow` |

Use `smoke` for basic HTTP/proxy health. Use `live-flow` when validating
frontend-to-backend behavior that must prove authenticated project APIs,
compile queue/status, and LLM file edit job behavior through the UI origin.

## Docs

- Local runtime commands: `docs/harness/local-harness.md`
- Browser validation with Chrome DevTools MCP: `docs/harness/browser-validation.md`
- Metrics and traces validation: `docs/harness/observability-validation.md`
- Compose/Helm drift policy: `docs/harness/runtime-parity.md`
- Required evidence by change type: `docs/harness/quality-gates.md`

## Codex Skill

Install the repo-owned skill into your local Codex home:

```bash
bash scripts/install-tertius-harness-skill.sh
```

The source remains in `tools/codex/skills/tertius-harness`; the install script
copies it to `${CODEX_HOME:-$HOME/.codex}/skills/tertius-harness`. Example
trigger phrase: "Use the Tertius harness to validate this UI change."
