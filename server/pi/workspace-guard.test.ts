import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createPiRpcProcess } from "./rpc-test-process.ts";
import { verifyPiRuntimeInstall } from "./pi-install-security.ts";

import {
  GUARD_FAILURE_REASON,
  GUARD_STATUS_COMMAND,
  buildWorkspaceManifest,
  buildPiExtensionArgs,
  createToolCallGuard,
} from "./workspace-guard.ts";

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "tertius-guard-"));
  await mkdir(path.join(root, "src"));
  await writeFile(path.join(root, "src", "main.py"), "print('ok')\n");
  const manifest = await buildWorkspaceManifest(root);
  return { root, manifest, guard: createToolCallGuard({ workspaceRoot: root, manifest }) };
}

test("RPC lifecycle rejects pending requests when the child exits early", async () => {
  const child = spawn(process.execPath, ["-e", "process.exit(7)"], { stdio: ["pipe", "pipe", "pipe"] });
  const rpc = createPiRpcProcess(child, { requestTimeoutMs: 10_000, stopTimeoutMs: 100 });
  await assert.rejects(rpc.request("state", "get_state"), /exited before responding/);
  await rpc.stop();
});

test("install verification rejects a nested Pi runtime copy", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tertius-pi-install-"));
  const nested = path.join(root, "node_modules", "@earendil-works", "pi-coding-agent", "node_modules", "@earendil-works", "pi-ai");
  await mkdir(nested, { recursive: true });
  await assert.rejects(verifyPiRuntimeInstall(root), /nested Pi runtime copy/);
});

test("install verification rejects a mismatched top-level Pi runtime version", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tertius-pi-install-"));
  for (const name of ["pi-agent-core", "pi-ai", "pi-tui"]) {
    const directory = path.join(root, "node_modules", "@earendil-works", name);
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, "package.json"), JSON.stringify({ name: `@earendil-works/${name}`, version: name === "pi-ai" ? "0.80.5" : "0.80.6" }));
  }
  await assert.rejects(verifyPiRuntimeInstall(root, { checkResolution: false }), /pi-ai.*0\.80\.5/);
});

test("U-014 allows reading a nested manifest path", async () => {
  const { guard } = await fixture();
  assert.equal(await guard({ toolName: "read", input: { path: "src/main.py" } }), undefined);
});

test("U-015 blocks an absolute auth path", async () => {
  const { guard } = await fixture();
  assert.equal((await guard({ toolName: "read", input: { path: "/var/lib/pi-agent/auth.json" } }))?.block, true);
});

test("U-016 blocks normalized traversal", async () => {
  const { guard } = await fixture();
  assert.equal((await guard({ toolName: "read", input: { path: "../../var/lib/pi-agent/auth.json" } }))?.block, true);
});

test("U-017 blocks a symlinked parent escaping the workspace", async () => {
  const { root, manifest } = await fixture();
  const outside = await mkdtemp(path.join(os.tmpdir(), "tertius-outside-"));
  await writeFile(path.join(outside, "secret"), "secret");
  await symlink(outside, path.join(root, "linked"));
  const guard = createToolCallGuard({ workspaceRoot: root, manifest });
  assert.equal((await guard({ toolName: "read", input: { path: "linked/secret" } }))?.block, true);
});

test("U-018 allows writing an existing manifest file by canonical path", async () => {
  const { guard } = await fixture();
  assert.equal(await guard({ toolName: "write", input: { path: "src/./main.py" } }), undefined);
});

test("U-019 blocks creating a new file", async () => {
  const { guard } = await fixture();
  assert.equal((await guard({ toolName: "write", input: { path: "src/new.py" } }))?.block, true);
});

test("U-020 blocks bash and unknown tools", async () => {
  const { guard } = await fixture();
  assert.equal((await guard({ toolName: "bash", input: { command: "pwd" } }))?.block, true);
  assert.equal((await guard({ toolName: "future_tool", input: { path: "src/main.py" } }))?.block, true);
});

test("U-021 blocks NUL, empty, and directory file paths", async () => {
  const { guard } = await fixture();
  assert.equal((await guard({ toolName: "read", input: { path: "src/\0main.py" } }))?.block, true);
  assert.equal((await guard({ toolName: "read", input: { path: "" } }))?.block, true);
  assert.equal((await guard({ toolName: "write", input: { path: "src" } }))?.block, true);
});

test("discovery tools default an absent path to the workspace root", async () => {
  const { guard } = await fixture();
  for (const toolName of ["grep", "find", "ls"]) {
    assert.equal(await guard({ toolName, input: {} }), undefined, toolName);
  }
  assert.equal((await guard({ toolName: "read", input: {} }))?.block, true);
  assert.equal((await guard({ toolName: "write", input: {} }))?.block, true);
  assert.equal((await guard({ toolName: "edit", input: {} }))?.block, true);
});

test("U-022 disables project discovery while loading the explicit guard in Pi RPC", async () => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "tertius-pi-rpc-"));
  const home = await mkdtemp(path.join(os.tmpdir(), "tertius-pi-home-"));
  const marker = path.join(workspace, "project-extension-loaded");
  const projectExtension = path.join(workspace, ".pi", "extensions", "marker.ts");
  await mkdir(path.dirname(projectExtension), { recursive: true });
  await writeFile(projectExtension, `import { writeFileSync } from "node:fs";\nwriteFileSync(${JSON.stringify(marker)}, "loaded");\nexport default function () {}\n`);

  const guardPath = path.resolve("workspace-guard.ts");
  const piPath = path.resolve("node_modules", ".bin", "pi");
  const child = spawn(piPath, [
    "--mode", "rpc", "--no-session",
    ...buildPiExtensionArgs(guardPath),
    "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files", "--no-approve",
  ], {
    cwd: workspace,
    env: { ...process.env, HOME: home, PI_CODING_AGENT_DIR: path.join(home, "pi"), PI_SKIP_VERSION_CHECK: "1", PI_TELEMETRY: "0" },
    stdio: ["pipe", "pipe", "pipe"],
  });

  const rpc = createPiRpcProcess(child, { requestTimeoutMs: 10_000, stopTimeoutMs: 1_000 });

  try {
    assert.equal((await rpc.request("state", "get_state")).success, true);
    const commands = await rpc.request("commands", "get_commands");
    assert.equal(commands.success, true);
    const data = commands.data as { commands: Array<{ name: string }> };
    assert.equal(data.commands.some((command) => command.name === GUARD_STATUS_COMMAND), true);
    await assert.rejects(access(marker));
  } finally {
    await rpc.stop();
  }
});

test("U-023 fails closed with a stable reason when canonicalization throws", async () => {
  const { root, manifest } = await fixture();
  const guard = createToolCallGuard({
    workspaceRoot: root,
    manifest,
    canonicalize: async () => { throw new Error("internal detail"); },
  });
  assert.deepEqual(await guard({ toolName: "read", input: { path: "src/main.py" } }), {
    block: true,
    reason: GUARD_FAILURE_REASON,
  });
});
