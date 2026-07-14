import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";

type RpcMessage = { id?: string; [key: string]: unknown };
type PendingRequest = {
  reject: (error: Error) => void;
  resolve: (message: RpcMessage) => void;
  timer: NodeJS.Timeout;
};

function hasExited(child: ChildProcessWithoutNullStreams): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}

function waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
  if (hasExited(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    const finish = (exited: boolean) => {
      clearTimeout(timer);
      child.off("exit", onExit);
      resolve(exited);
    };
    child.once("exit", onExit);
  });
}

export function createPiRpcProcess(
  child: ChildProcessWithoutNullStreams,
  options: { requestTimeoutMs: number; stopTimeoutMs: number },
) {
  const pending = new Map<string, PendingRequest>();
  let terminalError: Error | undefined;
  const lines = createInterface({ input: child.stdout });

  const rejectPending = (error: Error) => {
    terminalError ??= error;
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    pending.clear();
  };

  lines.on("line", (line) => {
    try {
      const message = JSON.parse(line) as RpcMessage;
      if (!message.id) return;
      const request = pending.get(message.id);
      if (!request) return;
      clearTimeout(request.timer);
      pending.delete(message.id);
      request.resolve(message);
    } catch (error) {
      rejectPending(new Error("Pi RPC emitted invalid JSON", { cause: error }));
    }
  });
  child.once("error", (error) => rejectPending(new Error("Pi RPC child failed", { cause: error })));
  child.once("exit", (code, signal) => {
    lines.close();
    rejectPending(new Error(`Pi RPC exited before responding (code=${code}, signal=${signal})`));
  });

  return {
    request(id: string, type: string): Promise<RpcMessage> {
      if (terminalError || hasExited(child)) {
        return Promise.reject(terminalError ?? new Error("Pi RPC exited before responding"));
      }
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`Pi RPC timed out: ${type}`));
        }, options.requestTimeoutMs);
        pending.set(id, { reject, resolve, timer });
        child.stdin.write(`${JSON.stringify({ id, type })}\n`, (error) => {
          if (error) rejectPending(new Error("Pi RPC stdin write failed", { cause: error }));
        });
      });
    },

    async stop(): Promise<void> {
      if (hasExited(child)) return;
      child.kill("SIGTERM");
      if (await waitForExit(child, options.stopTimeoutMs)) return;
      child.kill("SIGKILL");
      if (!await waitForExit(child, options.stopTimeoutMs)) {
        throw new Error("Pi RPC child did not exit after SIGKILL");
      }
    },
  };
}
