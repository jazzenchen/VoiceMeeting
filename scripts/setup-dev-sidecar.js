#!/usr/bin/env node

import { execSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const root = join(__dirname, "..");
const binariesDir = join(root, "tauri", "src-tauri", "binaries");
const minRealBinarySize = 1024 * 1024;

function targetTriple() {
  try {
    return execSync("rustc --print host-tuple", { encoding: "utf8" }).trim();
  } catch {
    if (process.platform === "darwin") {
      return process.arch === "arm64" ? "aarch64-apple-darwin" : "x86_64-apple-darwin";
    }
    if (process.platform === "win32") {
      return process.arch === "x64" ? "x86_64-pc-windows-msvc" : "i686-pc-windows-msvc";
    }
    return process.arch === "arm64" ? "aarch64-unknown-linux-gnu" : "x86_64-unknown-linux-gnu";
  }
}

const triple = targetTriple();
const isWindows = triple.includes("windows");
const filename = `voice-meeting-server-${triple}${isWindows ? ".exe" : ""}`;
const target = join(binariesDir, filename);

if (existsSync(target) && statSync(target).size > minRealBinarySize) {
  console.log(`Real sidecar already exists: ${filename}`);
  process.exit(0);
}

mkdirSync(binariesDir, { recursive: true });

if (isWindows) {
  writeFileSync(target, "");
} else {
  writeFileSync(
    target,
    [
      "#!/usr/bin/env sh",
      "echo 'VoiceMeeting development sidecar placeholder. Start the Python backend separately.' >&2",
      "exit 1",
      "",
    ].join("\n"),
  );
  chmodSync(target, 0o755);
}

console.log(`Created development sidecar placeholder: ${filename}`);
