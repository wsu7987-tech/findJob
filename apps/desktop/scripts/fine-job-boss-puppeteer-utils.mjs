import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import extractZip from "extract-zip";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import AnonymizeUaPlugin from "puppeteer-extra-plugin-anonymize-ua";

import {
  ensureRuntimeStorageExist,
  readStorageFile,
  writeStorageFile
} from "./fine-job-runtime-file-utils.mjs";

const require = createRequire(import.meta.url);
const LaodengPlugin = require("./puppeteer-extra-plugin-laodeng.cjs");

puppeteer.use(StealthPlugin());
puppeteer.use(LaodengPlugin());
puppeteer.use(AnonymizeUaPlugin({ makeWindows: false }));

export const BOSS_COOKIE_HOSTS = ["https://www.zhipin.com", "https://www.zhipin.com/"];
export const BOSS_AUTH_COOKIE_NAMES = new Set([
  "__zp_stoken__",
  "zp_stoken",
  "wt2",
  "wbg",
  "geek_zp_token"
]);

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const sleepWithRandomDelay = async (baseMs, randomMs = baseMs * 0.4) => {
  await sleep(Math.floor(baseMs + Math.random() * randomMs));
};

export const ensureDir = (dirPath) => {
  fs.mkdirSync(dirPath, { recursive: true });
};

export const runtimeFolderPath = path.join(os.homedir(), ".fine-job-geekgeekrun");
export const chromeExtensionDir = path.join(runtimeFolderPath, "chrome-extensions");
export const editThisCookieExtensionPath = path.join(chromeExtensionDir, "EditThisCookie");
const editThisCookieZipPath = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "extensions",
  "EditThisCookie.zip"
);
const APP_GEEKGEEKRUN_EDIT_VERSION = 1;

export const ensureEditThisCookie = async () => {
  ensureDir(runtimeFolderPath);
  ensureDir(chromeExtensionDir);
  const versionFilePath = path.join(editThisCookieExtensionPath, "GEEKGEEKRUN_EDIT_VERSION");
  const extractDoneFilePath = path.join(editThisCookieExtensionPath, "EXTRACT_DONE");
  let currentVersion = 0;
  try {
    currentVersion = Number(fs.readFileSync(versionFilePath, "utf8")) || 0;
  } catch {}
  const shouldExtract =
    currentVersion < APP_GEEKGEEKRUN_EDIT_VERSION || !fs.existsSync(extractDoneFilePath);
  if (!shouldExtract) {
    return;
  }
  if (fs.existsSync(editThisCookieExtensionPath)) {
    fs.rmSync(editThisCookieExtensionPath, { recursive: true, force: true });
  }
  await extractZip(editThisCookieZipPath, { dir: chromeExtensionDir });
  fs.writeFileSync(extractDoneFilePath, "");
  fs.writeFileSync(versionFilePath, String(APP_GEEKGEEKRUN_EDIT_VERSION));
};

export const writeJson = (filePath, value) => {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
};

export const readJson = (filePath, fallback = null) => {
  if (!fs.existsSync(filePath)) {
    return fallback;
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
};

export const writeStatus = (authDir, status, message, extra = {}) => {
  ensureRuntimeStorageExist(authDir);
  writeJson(path.join(authDir, "boss-login-status.json"), {
    status,
    message,
    updated_at: new Date().toISOString(),
    ...extra
  });
};

export const readBossCookies = (authDir) => readStorageFile(authDir, "boss-cookies.json");

export const readBossLocalStorageFile = (authDir) =>
  readStorageFile(authDir, "boss-local-storage.json");

export const writeBossCookies = (authDir, cookies) =>
  writeStorageFile(authDir, "boss-cookies.json", cookies);

export const writeBossLocalStorageFile = (authDir, localStorage) =>
  writeStorageFile(authDir, "boss-local-storage.json", localStorage);

export const launchBossBrowser = async ({ browserChannel = "chrome" } = {}) => {
  await ensureEditThisCookie();
  return puppeteer.launch({
    headless: false,
    pipe: true,
    enableExtensions: [editThisCookieExtensionPath],
    executablePath: findBrowserExecutable(browserChannel),
    defaultViewport: {
      width: 1440,
      height: 860
    },
    args: [
      "--start-maximized",
      "--no-default-browser-check",
      "--disable-search-engine-choice-screen",
      "--disable-blink-features=AutomationControlled"
    ],
    ignoreDefaultArgs: ["--enable-automation"]
  });
};

export const blockNavigation = async (page, predictor = (url) => true) => {
  await page.setRequestInterception(true);
  const handler = (req) => {
    if (req.isNavigationRequest() && req.frame() === page.mainFrame() && predictor(req)) {
      req.abort("aborted").catch(() => {});
    } else {
      req.continue().catch(() => {});
    }
  };
  page.on("request", handler);
  return {
    dispose: async () => {
      page.off("request", handler);
      await page.setRequestInterception(false).catch(() => {});
    }
  };
};

export const findBrowserExecutable = (browserChannel = "chrome") => {
  const preferred = normalizeBrowserChannel(browserChannel);
  const candidates = preferred === "msedge" ? edgeCandidates() : chromeCandidates();
  for (const candidate of [...candidates, ...chromeCandidates(), ...edgeCandidates()]) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error("未找到 Chrome 或 Edge 可执行文件");
};

export const normalizeBrowserChannel = (browserChannel = "chrome") => {
  const value = String(browserChannel || "chrome").toLowerCase();
  return value === "edge" || value === "msedge" ? "msedge" : "chrome";
};

const chromeCandidates = () => [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium-browser",
  "/usr/bin/chromium"
];

const edgeCandidates = () => [
  process.env.EDGE_PATH,
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/usr/bin/microsoft-edge"
];

export const hasBossAuthCookie = async (page) => {
  const cookies = await page.cookies(...BOSS_COOKIE_HOSTS);
  return cookies.some((cookie) => BOSS_AUTH_COOKIE_NAMES.has(String(cookie.name || "")));
};

export const saveBossAuthState = async ({ page, authDir }) => {
  const cookies = await page.cookies(...BOSS_COOKIE_HOSTS);
  const localStorage = await readBossLocalStorage(page);
  await Promise.all([
    writeBossCookies(authDir, cookies),
    writeBossLocalStorageFile(authDir, localStorage)
  ]);
};

export const readBossLocalStorage = async (page) => {
  if (!page.url().startsWith("https://www.zhipin.com")) {
    await page.goto("https://www.zhipin.com/desktop/", {
      waitUntil: "domcontentloaded",
      timeout: 30000
    });
  }
  return page.evaluate(() =>
    Object.fromEntries(
      Array.from({ length: window.localStorage.length }, (_, index) => {
        const key = window.localStorage.key(index);
        return [key, window.localStorage.getItem(key)];
      }).filter(([key]) => key)
    )
  );
};

export const setBossLocalStorage = async (page, localStorage) => {
  if (!localStorage || Object.keys(localStorage).length === 0) {
    return;
  }
  await page.goto("https://www.zhipin.com/desktop/", {
    waitUntil: "domcontentloaded",
    timeout: 30000
  });
  await page.evaluate((items) => {
    for (const [key, value] of Object.entries(items)) {
      window.localStorage.setItem(key, String(value));
    }
  }, localStorage);
};

export const setDomainLocalStorage = async (browser, url, kv) => {
  if (!kv || Object.keys(kv).length === 0) {
    return;
  }
  const page = await browser.newPage();
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    request.respond({
      status: 200,
      contentType: "text/plain",
      body: ":)"
    }).catch(() => {});
  });
  await page.goto(url);
  await page.evaluate((items) => {
    Object.keys(items).forEach((key) => {
      localStorage.setItem(key, items[key]);
    });
  }, kv);
  await page.close();
};

export const setBossCookies = async (page, cookies) => {
  for (const cookie of cookies || []) {
    const normalized = { ...cookie };
    if (Object.hasOwn(normalized, "sameSite")) {
      normalized.sameSite = "unspecified";
    }
    await page.setCookie(normalized);
  }
};

export const parseArgs = (argv) => {
  const result = {};
  for (let index = 2; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      continue;
    }
    const key = item.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      result[key] = true;
    } else {
      result[key] = next;
      index += 1;
    }
  }
  return result;
};
