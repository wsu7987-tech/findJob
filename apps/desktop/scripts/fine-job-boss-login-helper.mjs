import {
  blockNavigation,
  ensureDir,
  hasBossAuthCookie,
  launchBossBrowser,
  parseArgs,
  saveBossAuthState,
  sleepWithRandomDelay,
  writeStatus
} from "./fine-job-boss-puppeteer-utils.mjs";

const LOGIN_SUCCESS_URL_PREFIXES = [
  "https://www.zhipin.com/wapi/zppassport/qrcode/loginConfirm",
  "https://www.zhipin.com/wapi/zppassport/qrcode/dispatcher",
  "https://www.zhipin.com/wapi/zppassport/login/phoneV2"
];

const main = async () => {
  const args = parseArgs(process.argv);
  const authDir = args["auth-dir"];
  const loginUrl = "https://www.zhipin.com/web/user/";
  const browserChannel = args["browser-channel"] || "chrome";
  if (!authDir) {
    throw new Error("--auth-dir is required");
  }

  ensureDir(authDir);
  writeStatus(authDir, "running", "登录窗口已打开，请完成 BOSS 登录。");

  const browser = await launchBossBrowser({ browserChannel });
  try {
    const [page] = await browser.pages();
    const closeAttachedSet = new WeakSet();
    browser.on("targetcreated", async (target) => {
      const pages = await target.browser().pages();
      for (let index = 1; index < pages.length; index += 1) {
        const createdPage = pages[index];
        if (!closeAttachedSet.has(createdPage)) {
          closeAttachedSet.add(createdPage);
          createdPage.once("domcontentloaded", () => {
            createdPage.close().catch(() => {});
          });
        }
      }
    });

    page.once("close", () => {
      writeStatus(authDir, "closed", "登录窗口已关闭。");
    });

    await blockNavigation(page, (req) => !req.url().startsWith("https://www.zhipin.com"));
    await page.goto(loginUrl, {
      waitUntil: "domcontentloaded",
      timeout: 45000
    });
    writeStatus(authDir, "opened", "BOSS 登录页已打开，请完成登录。", { url: page.url() });

    const loginSuccessPromiseList = LOGIN_SUCCESS_URL_PREFIXES.map((prefix) =>
      page.waitForResponse((response) => response.url().startsWith(prefix), { timeout: 0 })
    );

    Promise.all([
      Promise.race(loginSuccessPromiseList),
      page.waitForNavigation({ timeout: 0 })
    ])
      .then(async ([response]) => {
        await sleepWithRandomDelay(2000, 0);
        const headerLogoAnchorHandler = await page.$(".header-home-logo");
        await Promise.all([
          headerLogoAnchorHandler ? headerLogoAnchorHandler.click() : page.goto("https://www.zhipin.com/"),
          page.waitForNavigation({ timeout: 0 })
        ]);
        return response;
      })
      .then(async (response) => {
        if (page.url().startsWith("https://www.zhipin.com/web/common/security-check.html")) {
          writeStatus(authDir, "security_check", "BOSS 出现安全验证，请在窗口中完成验证。", {
            url: page.url()
          });
          await page.waitForNavigation({ timeout: 0 });
        }
        await sleepWithRandomDelay(2000, 0);
        await saveBossAuthState({ page, authDir });
        writeStatus(authDir, "ready", "BOSS 登录状态已保存。", {
          trigger_url: response.url(),
          url: page.url()
        });
        await browser.close();
      })
      .catch((error) => {
        writeStatus(authDir, "failed", `登录监听失败：${error.message || error}`, {
          url: page.url()
        });
      });

    while (browser.isConnected() && !page.isClosed()) {
      await sleepWithRandomDelay(1200, 800);
      if (await hasBossAuthCookie(page)) {
        writeStatus(authDir, "logged_in_waiting_redirect", "已检测到登录 Cookie，等待 BOSS 页面跳转。", {
          url: page.url()
        });
      }
    }
  } catch (error) {
    writeStatus(authDir, "failed", `打开 BOSS 登录窗口失败：${error.message || error}`);
    try {
      await browser.close();
    } catch {}
    process.exitCode = 1;
  }
};

main().catch((error) => {
  const args = parseArgs(process.argv);
  if (args["auth-dir"]) {
    writeStatus(args["auth-dir"], "failed", `登录助手异常：${error.message || error}`);
  }
  process.exit(1);
});
