import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createElectronDevController } from "./dev-electron-controller.js";
import { buildDesktopDevEnv } from "./dev-env.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const projectRoot = path.resolve(__dirname, "..");
const workspaceRoot = path.resolve(projectRoot, "../..");
const devServerUrl = process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173";
const mainOutput = path.resolve(projectRoot, "dist-electron/main.cjs");
const preloadOutput = path.resolve(projectRoot, "dist-electron/preload.cjs");
const nodeExecutable = process.execPath;
const viteCli = path.join(path.dirname(require.resolve("vite/package.json")), "bin", "vite.js");
const esbuildCli = path.join(path.dirname(require.resolve("esbuild/package.json")), "bin", "esbuild");
const electronPkgPath = path.dirname(require.resolve("electron/package.json"));
const electronExecutable = path.join(
  electronPkgPath,
  "dist",
  process.platform === "win32" ? "electron.exe" : "electron"
);

const spawnProcess = (command, args, cwd, extraEnv = {}) =>
  spawn(command, args, {
    cwd,
    stdio: "inherit",
    shell: false,
    env: {
      ...process.env,
      ...extraEnv
    }
  });

const mainWatchProcess = spawnProcess(
  nodeExecutable,
  [
    esbuildCli,
    "electron/main.ts",
    "--bundle",
    "--format=cjs",
    "--platform=node",
    "--target=node22",
    "--outfile=dist-electron/main.cjs",
    "--sourcemap",
    "--external:electron",
    "--external:electron/main",
    "--external:electron/renderer",
    "--tsconfig=tsconfig.json",
    "--watch=forever"
  ],
  projectRoot
);

const preloadWatchProcess = spawnProcess(
  nodeExecutable,
  [
    esbuildCli,
    "electron/preload.ts",
    "--bundle",
    "--format=cjs",
    "--platform=node",
    "--target=node22",
    "--outfile=dist-electron/preload.cjs",
    "--sourcemap",
    "--external:electron",
    "--external:electron/main",
    "--external:electron/renderer",
    "--tsconfig=tsconfig.json",
    "--watch=forever"
  ],
  projectRoot
);

const viteProcess = spawnProcess(
  nodeExecutable,
  [viteCli, "--config", "vite.config.ts", "--host", "127.0.0.1", "--port", "5173"],
  projectRoot
);

const isDevServerReady = () =>
  new Promise((resolve) => {
    const request = http.get(devServerUrl, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });

    request.on("error", () => resolve(false));
    request.end();
  });

const fileExists = (target) => fs.existsSync(target);

for (let attempt = 0; attempt < 80; attempt += 1) {
  if ((await isDevServerReady()) && fileExists(mainOutput) && fileExists(preloadOutput)) {
    break;
  }

  await new Promise((resolve) => setTimeout(resolve, 500));
}

const electronController = createElectronDevController({
  spawnElectron: () =>
    spawnProcess(
      electronExecutable,
      [projectRoot],
      projectRoot,
      buildDesktopDevEnv({
        devServerUrl,
        workspaceRoot,
        processEnv: process.env
      })
    ),
  onElectronExit: () => shutdown()
});

const watchBuiltOutput = (target) => {
  fs.watchFile(target, { interval: 200 }, (current, previous) => {
    if (current.mtimeMs === 0 || current.mtimeMs === previous.mtimeMs) {
      return;
    }

    console.log(`[dev] detected rebuild for ${path.basename(target)}, restarting Electron`);
    electronController.scheduleRestart(path.basename(target));
  });
};

watchBuiltOutput(mainOutput);
watchBuiltOutput(preloadOutput);
electronController.start();

const shutdown = async () => {
  fs.unwatchFile(mainOutput);
  fs.unwatchFile(preloadOutput);
  electronController.shutdown();
  viteProcess.kill();
  mainWatchProcess.kill();
  preloadWatchProcess.kill();
  process.exit(0);
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

viteProcess.on("exit", shutdown);
mainWatchProcess.on("exit", shutdown);
preloadWatchProcess.on("exit", shutdown);
