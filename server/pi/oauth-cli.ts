import { createInterface } from "node:readline";
import type { Readable, Writable } from "node:stream";
import { pathToFileURL } from "node:url";

import { AuthStorage } from "@earendil-works/pi-coding-agent";

type OAuthCallbacks = Parameters<AuthStorage["login"]>[1];

export interface AuthStorageLike {
  login(provider: string, callbacks: OAuthCallbacks): Promise<void>;
  logout(provider: string): void;
}

export type PromptReader = (
  message: string,
  options?: { secret?: boolean; signal?: AbortSignal },
) => Promise<string>;

interface CliDependencies {
  storage?: AuthStorageLike;
  reader?: PromptReader;
  promptReaderManager?: PromptReaderManager;
  write?: (value: string) => void;
  signal?: AbortSignal;
}

interface PromptReaderDependencies {
  input?: Readable & { isTTY?: boolean; isRaw?: boolean; setRawMode?: (value: boolean) => unknown };
  output?: Writable;
  write?: (value: string) => void;
}

export interface PromptReaderManager {
  read: PromptReader;
  cancelAll(): void;
  pendingCount(): number;
}

const PROVIDER = "openai-codex";
const USAGE = "Usage: oauth-cli.ts <login|logout> openai-codex";

function isSecretPrompt(message: string, placeholder?: string): boolean {
  return /secret|token|password|authorization code|oauth code/i.test(`${message} ${placeholder ?? ""}`);
}

function createPromptReader(dependencies: PromptReaderDependencies = {}): PromptReader {
  const input = dependencies.input ?? process.stdin;
  const write = dependencies.write ?? ((value: string) => (dependencies.output ?? process.stdout).write(value));
  return async (message, options = {}) => {
    write(`${message}: `);

    if (input.isTTY && typeof input.setRawMode === "function") {
      const wasRaw = input.isRaw;
      input.setRawMode(true);
      input.resume();
      try {
        return await new Promise<string>((resolve, reject) => {
          let value = "";
          let settled = false;
          const finish = (result: { value: string } | { error: Error }) => {
            if (settled) return;
            settled = true;
            input.off("data", onData);
            input.off("error", onError);
            options.signal?.removeEventListener("abort", onAbort);
            if ("error" in result) reject(result.error);
            else resolve(result.value);
          };
          const onAbort = () => finish({ error: new Error("OAuth input cancelled") });
          const onError = () => finish({ error: new Error("OAuth input failed") });
          const onData = (chunk: Buffer | string) => {
            for (const character of chunk.toString()) {
              if (character === "\r" || character === "\n") {
                write("\n");
                finish({ value });
                return;
              } else if (character === "\u0003") {
                finish({ error: new Error("OAuth input cancelled") });
                return;
              } else if (character === "\u007f" || character === "\b") {
                if (value.length > 0) {
                  value = value.slice(0, -1);
                  write("\b \b");
                }
              } else if (character >= " ") {
                value += character;
                write(options.secret ? "*" : character);
              }
            }
          };
          input.on("data", onData);
          input.on("error", onError);
          options.signal?.addEventListener("abort", onAbort, { once: true });
          if (options.signal?.aborted) onAbort();
        });
      } finally {
        input.setRawMode(Boolean(wasRaw));
        input.pause();
      }
    }

    const readline = createInterface({ input, terminal: false });
    return await new Promise<string>((resolve, reject) => {
      let settled = false;
      const finish = (result: { value: string } | { error: Error }) => {
        if (settled) return;
        settled = true;
        options.signal?.removeEventListener("abort", onAbort);
        readline.close();
        if ("error" in result) reject(result.error);
        else resolve(result.value);
      };
      const onAbort = () => finish({ error: new Error("OAuth input cancelled") });
      readline.once("line", (value) => finish({ value }));
      readline.once("close", () => finish({ error: new Error("OAuth input ended before input was received") }));
      readline.once("error", () => finish({ error: new Error("OAuth input failed") }));
      options.signal?.addEventListener("abort", onAbort, { once: true });
      if (options.signal?.aborted) onAbort();
    });
  };
}

export function createPromptReaderManager(dependencies: PromptReaderDependencies = {}): PromptReaderManager {
  const readPrompt = createPromptReader(dependencies);
  const active = new Set<AbortController>();
  return {
    async read(message, options = {}) {
      const controller = new AbortController();
      active.add(controller);
      const signal = options.signal
        ? AbortSignal.any([options.signal, controller.signal])
        : controller.signal;
      try {
        return await readPrompt(message, { ...options, signal });
      } finally {
        active.delete(controller);
      }
    },
    cancelAll() {
      for (const controller of active) controller.abort();
      active.clear();
    },
    pendingCount() {
      return active.size;
    },
  };
}

export async function runOAuthCli(args: string[], dependencies: CliDependencies = {}): Promise<void> {
  const [command, provider, ...extra] = args;
  if ((command !== "login" && command !== "logout") || provider !== PROVIDER || extra.length > 0) {
    throw new Error(USAGE);
  }

  const storage = dependencies.storage ?? AuthStorage.create();
  const promptReaderManager = dependencies.promptReaderManager ?? (dependencies.reader
    ? { read: dependencies.reader, cancelAll() {}, pendingCount() { return 0; } }
    : createPromptReaderManager());
  const reader = promptReaderManager.read;
  const write = dependencies.write ?? ((value: string) => process.stdout.write(value));

  if (command === "logout") {
    storage.logout(PROVIDER);
    write("OpenAI Codex logout completed.\n");
    return;
  }

  try {
    await storage.login(PROVIDER, {
      signal: dependencies.signal,
      onAuth(info) {
        write(`Open this URL in your browser:\n${info.url}\n`);
        if (info.instructions) write(`${info.instructions}\n`);
      },
      onDeviceCode(info) {
        write(`Open this URL in your browser:\n${info.verificationUri}\nDevice code: ${info.userCode}\n`);
      },
      onProgress(message) {
        write(`${message}\n`);
      },
      onPrompt(prompt) {
        return reader(`${prompt.message}${prompt.placeholder ? ` (${prompt.placeholder})` : ""}`, {
          secret: isSecretPrompt(prompt.message, prompt.placeholder),
          signal: dependencies.signal,
        });
      },
      onManualCodeInput() {
        return reader("Paste the authorization response or code", { secret: true, signal: dependencies.signal });
      },
      async onSelect(prompt) {
        write(`${prompt.message}\n`);
        prompt.options.forEach((option, index) => write(`  ${index + 1}. ${option.label}\n`));
        while (true) {
          const selected = Number.parseInt(await reader(`Select 1-${prompt.options.length}`, { signal: dependencies.signal }), 10) - 1;
          if (selected >= 0 && selected < prompt.options.length) return prompt.options[selected].id;
          write("Invalid selection.\n");
        }
      },
    });
  } finally {
    promptReaderManager.cancelAll();
  }
  write("OpenAI Codex login completed.\n");
}

async function main(): Promise<void> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);
  try {
    await runOAuthCli(process.argv.slice(2), { signal: controller.signal });
  } catch {
    process.stderr.write(`ERROR: OAuth operation failed. ${USAGE}\n`);
    process.exitCode = 1;
  } finally {
    process.off("SIGINT", abort);
    process.off("SIGTERM", abort);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  void main();
}
