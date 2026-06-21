# Browser Validation Reference

Read `docs/harness/browser-validation.md` before using Chrome DevTools MCP.

Use an isolated Chrome profile under `.tmp/chrome-harness`, bind remote
debugging only to `127.0.0.1`, and connect MCP with
`chrome-devtools-mcp@1.3.0 --browser-url=http://127.0.0.1:<port>
--no-usage-statistics --no-performance-crux`. Do not reuse personal profiles or
sessions with production credentials.

Use `.tmp/harness/k3s.env` to discover k3s URLs when it exists. Otherwise use
`http://localhost:5173` for Compose dev or `http://localhost:18080` for Compose
parity.

Evidence expectations:

- UI changes: console errors and visual/DOM evidence.
- API/UI changes: failed network requests.
- Visual bug fixes: before/after evidence when reproducible.
- Viewer changes: canvas/WebGL nonblank check.
