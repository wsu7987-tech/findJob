import { build } from "esbuild";
import { mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const outdir = path.resolve(projectRoot, "dist-electron");

await mkdir(outdir, { recursive: true });
await rm(path.resolve(outdir, "main.js"), { force: true });
await rm(path.resolve(outdir, "main.js.map"), { force: true });
await rm(path.resolve(outdir, "preload.js"), { force: true });
await rm(path.resolve(outdir, "preload.js.map"), { force: true });

const shared = {
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node22",
  sourcemap: true,
  external: ["electron", "electron/main", "electron/renderer"],
  tsconfig: path.resolve(projectRoot, "tsconfig.json")
};

await Promise.all([
  build({
    ...shared,
    entryPoints: [path.resolve(projectRoot, "electron/main.ts")],
    outfile: path.resolve(outdir, "main.cjs")
  }),
  build({
    ...shared,
    entryPoints: [path.resolve(projectRoot, "electron/preload.ts")],
    outfile: path.resolve(outdir, "preload.cjs")
  })
]);
