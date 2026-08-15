import path from "node:path";
import { URLSearchParams } from "node:url";

import {
  ensureDir,
  launchBossBrowser,
  parseArgs,
  readBossCookies,
  readBossLocalStorageFile,
  setBossCookies,
  setDomainLocalStorage,
  sleepWithRandomDelay,
  writeJson
} from "./fine-job-boss-puppeteer-utils.mjs";

const BOSS_SEARCH_URL = "https://www.zhipin.com/web/geek/job";
const CITY_CODES = {
  "全国": "100010000",
  "北京": "101010100",
  "上海": "101020100",
  "广州": "101280100",
  "深圳": "101280600",
  "杭州": "101210100",
  "成都": "101270100",
  "武汉": "101200100",
  "南京": "101190100",
  "苏州": "101190400",
  "远程": "100010000"
};
const RISK_MARKERS = ["验证码", "安全验证", "风险", "异常访问", "扫码", "请输入手机号", "登录/注册"];

const main = async () => {
  const args = parseArgs(process.argv);
  const authDir = args["auth-dir"];
  const outPath = args.out;
  const keyword = args.keyword;
  const city = args.city || "全国";
  const browserChannel = args["browser-channel"] || "chrome";
  const maxJobs = Number(args["max-jobs"] || 3);
  if (!authDir || !outPath || !keyword) {
    throw new Error("--auth-dir, --out and --keyword are required");
  }
  ensureDir(path.dirname(outPath));
  const cookies = readBossCookies(authDir);
  const localStorage = readBossLocalStorageFile(authDir);

  const browser = await launchBossBrowser({ browserChannel });
  try {
    const [page] = await browser.pages();
    await setBossCookies(page, cookies);
    await setDomainLocalStorage(browser, "https://www.zhipin.com/desktop/", localStorage);
    await page.bringToFront();
    await sleepWithRandomDelay(900);
    await page.goto(buildSearchUrl({ keyword, city }), {
      waitUntil: "domcontentloaded",
      timeout: 45000
    });
    await sleepWithRandomDelay(1600);
    await raiseIfRiskPage(page);
    await humanScroll(page);
    const cards = await extractCards(page, maxJobs);
    const jobs = [];
    for (const card of cards) {
      await sleepWithRandomDelay(900);
      const detailPage = await browser.newPage();
      try {
        await setBossCookies(detailPage, cookies);
        await detailPage.goto(card.job_url, {
          waitUntil: "domcontentloaded",
          timeout: 30000
        });
        await sleepWithRandomDelay(900);
        await raiseIfRiskPage(detailPage);
        const jdText = await safeBodyText(detailPage);
        jobs.push({ ...card, keyword, city, jd_text: jdText.slice(0, 6000) });
      } finally {
        await detailPage.close().catch(() => {});
      }
    }
    writeJson(outPath, { ok: true, jobs });
    await browser.close();
  } catch (error) {
    writeJson(outPath, { ok: false, error: error.message || String(error), jobs: [] });
    await browser.close().catch(() => {});
    process.exitCode = 1;
  }
};

const buildSearchUrl = ({ keyword, city }) => {
  const params = new URLSearchParams({
    query: keyword,
    city: CITY_CODES[city] || CITY_CODES["全国"]
  });
  return `${BOSS_SEARCH_URL}?${params.toString()}`;
};

const humanScroll = async (page) => {
  const times = 2 + Math.floor(Math.random() * 3);
  for (let index = 0; index < times; index += 1) {
    await page.mouse.wheel({ deltaY: 350 + Math.floor(Math.random() * 410) });
    await sleepWithRandomDelay(600);
  }
};

const raiseIfRiskPage = async (page) => {
  const text = await safeBodyText(page);
  if (RISK_MARKERS.some((marker) => text.includes(marker))) {
    throw new Error("BOSS 页面出现登录、验证码或风险提示，采集已暂停。");
  }
};

const extractCards = async (page, maxJobs) => {
  return page.evaluate((limit) => {
    const normalizeUrl = (href) => {
      if (!href) return "";
      if (href.startsWith("//")) return `https:${href}`;
      if (href.startsWith("/")) return `https://www.zhipin.com${href}`;
      return href;
    };
    const firstText = (root, selectors) => {
      for (const selector of selectors) {
        const value = root.querySelector(selector)?.textContent?.trim();
        if (value) return value;
      }
      return "";
    };
    const guessByMarkers = (lines, markers) => {
      return lines.find((line) => markers.some((marker) => line.includes(marker))) || "";
    };
    const guessSalary = (lines) => lines.find((line) => /[kK]|薪/.test(line)) || "";
    const nodes = Array.from(
      document.querySelectorAll(".job-card-wrapper, .job-primary, li.job-card-wrapper, [class*='job-card']")
    );
    const seen = new Set();
    const results = [];
    for (const node of nodes) {
      if (results.length >= limit) break;
      const text = node.textContent?.trim() || "";
      if (!text) continue;
      const url = normalizeUrl(node.querySelector("a")?.getAttribute("href") || "");
      if (!url || seen.has(url)) continue;
      seen.add(url);
      const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      results.push({
        job_url: url,
        job_title: (firstText(node, [".job-name", ".job-title", "[class*='job-name']"]) || lines[0] || "").slice(0, 160),
        company_name: (firstText(node, [".company-name", "[class*='company-name']"]) || lines[1] || "").slice(0, 160),
        salary_text: (firstText(node, [".salary", ".red", "[class*='salary']"]) || guessSalary(lines)).slice(0, 80),
        location_text: guessByMarkers(lines, ["上海", "北京", "杭州", "深圳", "广州", "远程"]).slice(0, 80),
        experience_text: guessByMarkers(lines, ["经验", "年"]).slice(0, 80),
        education_text: guessByMarkers(lines, ["本科", "大专", "硕士", "博士", "学历"]).slice(0, 80),
        hr_active_text: guessByMarkers(lines, ["活跃", "刚刚", "在线", "回复"]).slice(0, 120)
      });
    }
    return results;
  }, maxJobs);
};

const safeBodyText = async (page) => {
  try {
    return await page.$eval("body", (body) => body.innerText || "");
  } catch {
    return "";
  }
};

main().catch((error) => {
  const args = parseArgs(process.argv);
  if (args.out) {
    writeJson(args.out, { ok: false, error: error.message || String(error), jobs: [] });
  }
  process.exit(1);
});
