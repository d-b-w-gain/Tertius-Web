import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, stat } from "node:fs/promises";
import { Readable } from "node:stream";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { AuthStorage } from "@earendil-works/pi-coding-agent";

import { createPromptReaderManager, runOAuthCli, type AuthStorageLike, type PromptReader } from "./oauth-cli.ts";

function fixture(inputs: string[] = []) {
  const output: string[] = [];
  const prompts: Array<{ message: string; secret: boolean }> = [];
  const reader: PromptReader = async (message, options = {}) => {
    prompts.push({ message, secret: options.secret === true });
    return inputs.shift() ?? "";
  };
  return { output, prompts, reader, write: (value: string) => output.push(value) };
}

test("login delegates provider callbacks and restores manual code input", async () => {
  const io = fixture(["visible answer", "oauth-secret", "manual-secret", "2"]);
  const controller = new AbortController();
  let calledProvider = "";
  const storage: AuthStorageLike = {
    async login(provider, callbacks) {
      calledProvider = provider;
      assert.equal(callbacks.signal, controller.signal);
      callbacks.onAuth({ url: "https://auth.example/login", instructions: "Open the operator account." });
      callbacks.onDeviceCode({ verificationUri: "https://auth.example/device", userCode: "ABCD-EFGH" });
      callbacks.onProgress?.("Waiting for authorization...");
      assert.equal(await callbacks.onPrompt({ message: "Tenant", placeholder: "name" }), "visible answer");
      assert.equal(await callbacks.onPrompt({ message: "Token", placeholder: "secret" }), "oauth-secret");
      assert.equal(await callbacks.onManualCodeInput?.(), "manual-secret");
      assert.equal(await callbacks.onSelect({ message: "Method", options: [
        { id: "browser", label: "Browser" },
        { id: "device_code", label: "Device code" },
      ] }), "device_code");
    },
    logout() { throw new Error("unexpected logout"); },
  };

  await runOAuthCli(["login", "openai-codex"], { storage, signal: controller.signal, ...io });

  assert.equal(calledProvider, "openai-codex");
  assert.match(io.output.join(""), /https:\/\/auth\.example\/login/);
  assert.match(io.output.join(""), /ABCD-EFGH/);
  assert.match(io.output.join(""), /Waiting for authorization/);
  assert.doesNotMatch(io.output.join(""), /oauth-secret|manual-secret/);
  assert.deepEqual(io.prompts.map((prompt) => prompt.secret), [false, true, true, false]);
});

class FakeTty extends EventEmitter {
    isTTY = true;
    isRaw = false;
    rawChanges: boolean[] = [];
    setRawMode(value: boolean) { this.rawChanges.push(value); this.isRaw = value; return this; }
    resume() { return this; }
    pause() { return this; }
}

test("TTY prompt abort removes listeners and restores the previous raw mode", async () => {
  const input = new FakeTty();
  const output: string[] = [];
  const controller = new AbortController();
  const manager = createPromptReaderManager({ input: input as never, write: (value) => output.push(value) });

  const pending = manager.read("Secret", { secret: true, signal: controller.signal });
  controller.abort();

  await assert.rejects(pending, /cancelled/);
  assert.deepEqual(input.rawChanges, [true, false]);
  assert.equal(input.listenerCount("data"), 0);
});

test("browser callback completion cancels an unawaited manual-code reader", async () => {
  const input = new FakeTty();
  const manager = createPromptReaderManager({ input: input as never, write() {} });
  let manualRead: Promise<string> | undefined;
  const storage: AuthStorageLike = {
    async login(_provider, callbacks) {
      manualRead = callbacks.onManualCodeInput?.();
      await Promise.resolve();
      // Browser callback won; the SDK returns without awaiting manual input.
    },
    logout() {},
  };

  await runOAuthCli(["login", "openai-codex"], { storage, promptReaderManager: manager, write() {} });

  await assert.rejects(manualRead!, /cancelled/);
  assert.equal(input.listenerCount("data"), 0);
  assert.equal(manager.pendingCount(), 0);
  assert.deepEqual(input.rawChanges, [true, false]);
});

test("non-TTY prompt rejects deterministic EOF instead of hanging", async () => {
  const input = Readable.from([]);
  const manager = createPromptReaderManager({ input, write() {} });
  await assert.rejects(manager.read("Value"), /ended before input was received/);
});

test("logout delegates to AuthStorage and reports no credential contents", async () => {
  const io = fixture();
  let loggedOut = "";
  const storage: AuthStorageLike = {
    async login() { throw new Error("unexpected login"); },
    logout(provider) { loggedOut = provider; },
  };

  await runOAuthCli(["logout", "openai-codex"], { storage, ...io });

  assert.equal(loggedOut, "openai-codex");
  assert.equal(io.output.join(""), "OpenAI Codex logout completed.\n");
});

test("CLI rejects unsupported commands and providers before calling storage", async () => {
  const io = fixture();
  let calls = 0;
  const storage: AuthStorageLike = {
    async login() { calls += 1; },
    logout() { calls += 1; },
  };

  await assert.rejects(runOAuthCli(["login", "anthropic"], { storage, ...io }), /openai-codex/);
  await assert.rejects(runOAuthCli(["list"], { storage, ...io }), /Usage:/);
  assert.equal(calls, 0);
});

test("pinned SDK file storage persists auth.json with mode 0600", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tertius-oauth-cli-"));
  const authPath = path.join(directory, "auth.json");
  const storage = AuthStorage.create(authPath);

  storage.set("openai-codex", { type: "oauth", access: "access", refresh: "refresh", expires: 1 });

  assert.equal((await stat(authPath)).mode & 0o777, 0o600);
});
