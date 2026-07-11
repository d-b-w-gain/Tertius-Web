import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { lstat, readdir, realpath } from "node:fs/promises";
import path from "node:path";

export const GUARD_FAILURE_REASON = "TERTIUS_GUARD_FAILURE";
export const GUARD_STATUS_COMMAND = "tertius-workspace-guard";
export const DEFAULT_GUARD_EXTENSION_PATH = "/opt/tertius-pi/workspace-guard.ts";

export function buildPiExtensionArgs(extensionPath = DEFAULT_GUARD_EXTENSION_PATH): string[] {
  return ["--no-extensions", "-e", extensionPath];
}

const ALLOWED_TOOLS = new Set(["read", "edit", "write", "grep", "find", "ls"]);
const MUTATING_TOOLS = new Set(["edit", "write"]);
const FILE_TOOLS = new Set(["read", "edit", "write"]);

class PathViolation extends Error {}

export type WorkspaceManifest = ReadonlySet<string>;
export type ToolCall = { toolName: string; input: unknown };
export type GuardResult = { block: true; reason: string } | undefined;
export type Canonicalizer = (workspaceRoot: string, requestedPath: string) => Promise<string>;

function blocked(reason: string): Exclude<GuardResult, undefined> {
  return { block: true, reason };
}

export async function canonicalizeWorkspacePath(
  workspaceRoot: string,
  requestedPath: string,
): Promise<string> {
  if (!requestedPath || requestedPath.includes("\0") || path.isAbsolute(requestedPath)) {
    throw new PathViolation("invalid path");
  }

  const segments = requestedPath.split(/[\\/]+/);
  if (segments.includes("..")) throw new PathViolation("path traversal");

  const root = await realpath(workspaceRoot);
  const target = path.resolve(root, requestedPath);
  if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
    throw new PathViolation("outside workspace");
  }

  let current = root;
  for (const segment of path.relative(root, target).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = await lstat(current);
    } catch {
      throw new PathViolation("missing path component");
    }
    if (stat.isSymbolicLink()) throw new PathViolation("symlink path component");
  }

  let canonical;
  try {
    canonical = await realpath(target);
  } catch {
    throw new PathViolation("missing target");
  }
  if (canonical !== root && !canonical.startsWith(`${root}${path.sep}`)) {
    throw new PathViolation("canonical path outside workspace");
  }
  return canonical;
}

export async function buildWorkspaceManifest(workspaceRoot: string): Promise<WorkspaceManifest> {
  const root = await realpath(workspaceRoot);
  const files = new Set<string>();

  async function visit(directory: string): Promise<void> {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error("workspace manifest contains a symlink");
      if (entry.isDirectory()) await visit(entryPath);
      else if (entry.isFile()) files.add(await realpath(entryPath));
      else throw new Error("workspace manifest contains a non-regular entry");
    }
  }

  await visit(root);
  return files;
}

export function createToolCallGuard(options: {
  workspaceRoot: string;
  manifest: WorkspaceManifest;
  canonicalize?: Canonicalizer;
}): (event: ToolCall) => Promise<GuardResult> {
  const canonicalize = options.canonicalize ?? canonicalizeWorkspacePath;

  return async (event: ToolCall): Promise<GuardResult> => {
    try {
      if (!ALLOWED_TOOLS.has(event.toolName)) return blocked("TERTIUS_TOOL_BLOCKED");
      if (typeof event.input !== "object" || event.input === null) return blocked("TERTIUS_PATH_BLOCKED");

      const suppliedPath = (event.input as { path?: unknown }).path;
      const requestedPath = suppliedPath === undefined && !FILE_TOOLS.has(event.toolName)
        ? "."
        : suppliedPath;
      if (typeof requestedPath !== "string") return blocked("TERTIUS_PATH_BLOCKED");

      let canonical: string;
      try {
        canonical = await canonicalize(options.workspaceRoot, requestedPath);
      } catch (error) {
        if (error instanceof PathViolation) return blocked("TERTIUS_PATH_BLOCKED");
        throw error;
      }

      const stat = await lstat(canonical);
      if (FILE_TOOLS.has(event.toolName) && !stat.isFile()) return blocked("TERTIUS_PATH_BLOCKED");
      if (MUTATING_TOOLS.has(event.toolName) && !options.manifest.has(canonical)) {
        return blocked("TERTIUS_PATH_BLOCKED");
      }
      if (event.toolName === "read" && !options.manifest.has(canonical)) {
        return blocked("TERTIUS_PATH_BLOCKED");
      }
      return undefined;
    } catch {
      return blocked(GUARD_FAILURE_REASON);
    }
  };
}

export default function workspaceGuardExtension(pi: ExtensionAPI): void {
  const workspaceRoot = process.cwd();
  const guard = buildWorkspaceManifest(workspaceRoot).then((manifest) =>
    createToolCallGuard({ workspaceRoot, manifest }),
  );

  pi.on("tool_call", async (event) => {
    try {
      return await (await guard)({ toolName: event.toolName, input: event.input });
    } catch {
      return blocked(GUARD_FAILURE_REASON);
    }
  });

  pi.registerCommand(GUARD_STATUS_COMMAND, {
    description: "Report that the Tertius workspace guard is loaded",
    handler: async () => undefined,
  });
}
