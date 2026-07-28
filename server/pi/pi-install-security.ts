import { access, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const EXPECTED_VERSION = "0.80.6";
const RUNTIME_PACKAGES = ["pi-agent-core", "pi-ai", "pi-tui"] as const;
const OPENAI_RESPONSES_SHARED_PATH = path.join(
  "dist",
  "api",
  "openai-responses-shared.js",
);
const REASONING_PROVENANCE_BRANCHES = [
  { eventType: "response.reasoning_summary_text.delta", isSummary: true },
  { eventType: "response.reasoning_summary_part.done", isSummary: true },
  { eventType: "response.reasoning_text.delta", isSummary: false },
] as const;

function packageDirectory(root: string, name: string): string {
  return path.join(root, "node_modules", "@earendil-works", name);
}

function nestedPackageDirectory(root: string, name: string): string {
  return path.join(packageDirectory(root, "pi-coding-agent"), "node_modules", "@earendil-works", name);
}

async function exists(candidate: string): Promise<boolean> {
  try {
    await access(candidate);
    return true;
  } catch {
    return false;
  }
}

function reasoningBranchBounds(source: string, eventType: string): [number, number] {
  const startNeedle = `        else if (event.type === "${eventType}") {`;
  const start = source.indexOf(startNeedle);
  if (start === -1 || source.indexOf(startNeedle, start + startNeedle.length) !== -1) {
    throw new Error(`pinned Pi reasoning branch ${eventType} is missing or ambiguous`);
  }
  const end = source.indexOf("\n        else if (", start + startNeedle.length);
  if (end === -1) {
    throw new Error(`pinned Pi reasoning branch ${eventType} has no boundary`);
  }
  return [start, end];
}

function markerLine(isSummary: boolean): string {
  return `                tertiusReasoningSummary: ${isSummary},`;
}

export function hardenPiReasoningProvenanceSource(source: string): string {
  let hardened = source;
  for (const branch of REASONING_PROVENANCE_BRANCHES) {
    const [start, end] = reasoningBranchBounds(hardened, branch.eventType);
    const original = hardened.slice(start, end);
    const expectedMarker = markerLine(branch.isSummary);
    const markerCount = original.match(/tertiusReasoningSummary:/g)?.length ?? 0;
    if (markerCount === 1 && original.includes(expectedMarker)) continue;
    if (markerCount !== 0) {
      throw new Error(`pinned Pi reasoning branch ${branch.eventType} has an invalid provenance marker`);
    }
    const pushNeedle = [
      "            stream.push({",
      '                type: "thinking_delta",',
    ].join("\n");
    const pushCount = original.split(pushNeedle).length - 1;
    if (pushCount !== 1) {
      throw new Error(`pinned Pi reasoning branch ${branch.eventType} has an unexpected stream shape`);
    }
    const updated = original.replace(pushNeedle, `${pushNeedle}\n${expectedMarker}`);
    hardened = hardened.slice(0, start) + updated + hardened.slice(end);
  }
  return hardened;
}

export function verifyPiReasoningProvenanceSource(source: string): void {
  for (const branch of REASONING_PROVENANCE_BRANCHES) {
    const [start, end] = reasoningBranchBounds(source, branch.eventType);
    const branchSource = source.slice(start, end);
    const expectedMarker = markerLine(branch.isSummary);
    const markerCount = branchSource.match(/tertiusReasoningSummary:/g)?.length ?? 0;
    if (markerCount !== 1 || !branchSource.includes(expectedMarker)) {
      throw new Error(`pinned Pi reasoning branch ${branch.eventType} lacks its provenance marker`);
    }
  }
}

async function hardenPiReasoningProvenanceInstall(root: string): Promise<void> {
  const sourcePath = path.join(packageDirectory(root, "pi-ai"), OPENAI_RESPONSES_SHARED_PATH);
  const source = await readFile(sourcePath, "utf8");
  const hardened = hardenPiReasoningProvenanceSource(source);
  if (hardened !== source) await writeFile(sourcePath, hardened, "utf8");
}

async function verifyPiReasoningProvenanceInstall(root: string): Promise<void> {
  const sourcePath = path.join(packageDirectory(root, "pi-ai"), OPENAI_RESPONSES_SHARED_PATH);
  verifyPiReasoningProvenanceSource(await readFile(sourcePath, "utf8"));
}

export async function verifyPiRuntimeInstall(
  root: string,
  options: { checkResolution?: boolean } = {},
): Promise<void> {
  for (const name of RUNTIME_PACKAGES) {
    if (await exists(nestedPackageDirectory(root, name))) {
      throw new Error(`nested Pi runtime copy remains: @earendil-works/${name}`);
    }
  }

  for (const name of RUNTIME_PACKAGES) {
    const manifestPath = path.join(packageDirectory(root, name), "package.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as { version?: string };
    if (manifest.version !== EXPECTED_VERSION) {
      throw new Error(`@earendil-works/${name} resolved ${manifest.version ?? "without a version"}; expected ${EXPECTED_VERSION}`);
    }
  }

  await verifyPiReasoningProvenanceInstall(root);

  if (options.checkResolution === false) return;
  const codingAgentDirectory = packageDirectory(root, "pi-coding-agent");
  const requireFromCodingAgent = createRequire(path.join(codingAgentDirectory, "package.json"));
  for (const name of RUNTIME_PACKAGES) {
    const packageName = `@earendil-works/${name}`;
    const expectedManifest = path.join(packageDirectory(root, name), "package.json");
    let resolvedManifest: string | undefined;
    for (const searchPath of requireFromCodingAgent.resolve.paths(packageName) ?? []) {
      const candidate = path.join(searchPath, packageName, "package.json");
      if (await exists(candidate)) {
        resolvedManifest = requireFromCodingAgent.resolve(candidate);
        break;
      }
    }
    if (resolvedManifest !== expectedManifest) {
      throw new Error(`${packageName} resolves outside verified top-level package: ${resolvedManifest ?? "unresolved"}`);
    }
  }
}

export async function hardenPiRuntimeInstall(root: string): Promise<void> {
  for (const name of RUNTIME_PACKAGES) {
    await rm(nestedPackageDirectory(root, name), { recursive: true, force: true });
  }
  await hardenPiReasoningProvenanceInstall(root);
  await verifyPiRuntimeInstall(root);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const action = process.argv[2];
  const root = path.resolve(process.argv[3] ?? ".");
  if (action === "harden") await hardenPiRuntimeInstall(root);
  else if (action === "verify") await verifyPiRuntimeInstall(root);
  else throw new Error("usage: pi-install-security.ts harden|verify [package-root]");
}
