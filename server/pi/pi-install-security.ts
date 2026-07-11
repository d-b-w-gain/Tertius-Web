import { access, readFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const EXPECTED_VERSION = "0.80.6";
const RUNTIME_PACKAGES = ["pi-agent-core", "pi-ai", "pi-tui"] as const;

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
  await verifyPiRuntimeInstall(root);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const action = process.argv[2];
  const root = path.resolve(process.argv[3] ?? ".");
  if (action === "harden") await hardenPiRuntimeInstall(root);
  else if (action === "verify") await verifyPiRuntimeInstall(root);
  else throw new Error("usage: pi-install-security.ts harden|verify [package-root]");
}
