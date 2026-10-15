import { test, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

test("registration, restricted login, import, analysis, report review and logout", async ({
  page,
}) => {
  const bridge = spawn(
    ".venv/bin/python",
    ["-m", "scripts.browser_api_bridge"],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  const pending = new Map<number, (v: any) => void>();
  let counter = 0;
  let readyResolve: (v: any) => void;
  const ready = new Promise<any>((r) => (readyResolve = r));
  createInterface({ input: bridge.stdout }).on("line", (line) => {
    const value = JSON.parse(line);
    if (value.ready) readyResolve(value);
    else {
      pending.get(value.id)?.(value);
      pending.delete(value.id);
    }
  });
  bridge.stderr.on("data", () => {});
  const call = (
    method: string,
    url: string,
    body?: string,
    headers: any = {},
  ) =>
    new Promise<any>((resolve) => {
      const id = ++counter;
      pending.set(id, resolve);
      bridge.stdin.write(
        JSON.stringify({ id, method, path: url, body, headers }) + "\n",
      );
    });
  try {
    const { files } = await ready;
    await page.context().route("http://retailpilot.test/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.startsWith("/api/")) {
        const result = await call(
          request.method(),
          url.pathname + url.search,
          request.postData() || undefined,
          await request.allHeaders(),
        );
        if (result.error) throw new Error(result.error);
        delete result.headers["content-length"];
        delete result.headers["content-encoding"];
        await route.fulfill({
          status: result.status,
          headers: result.headers,
          body: Buffer.from(result.body, "base64"),
        });
        return;
      }
      const file = url.pathname.startsWith("/assets/")
        ? path.join("dist", url.pathname)
        : "dist/index.html";
      await route.fulfill({
        status: 200,
        contentType: file.endsWith(".js")
          ? "application/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : "text/html",
        body: readFileSync(file),
      });
    });
    await page.goto("http://retailpilot.test");
    await expect(page.getByText("会话已过期", { exact: false })).toHaveCount(0);
    await page.getByRole("button", { name: "注册新账号", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "创建你的账号" }),
    ).toBeVisible();
    await page.getByLabel("账号", { exact: true }).fill("browser-member");
    await page.getByLabel("姓名", { exact: true }).fill("注册测试用户");
    await page.getByLabel("密码", { exact: true }).fill("Member-password-123");
    await page
      .getByLabel("确认密码", { exact: true })
      .fill("Different-password-123");
    await page.getByRole("button", { name: "注册账号", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("两次密码不一致");
    await page
      .getByLabel("确认密码", { exact: true })
      .fill("Member-password-123");
    mkdirSync("test-results", { recursive: true });
    await page.screenshot({
      path: "test-results/registration-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole("button", { name: "注册账号", exact: true }),
    ).toBeInViewport();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: "test-results/registration-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "注册账号", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("注册成功");
    await expect(page.getByLabel("账号", { exact: true })).toHaveValue(
      "browser-member",
    );
    await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");

    // Duplicate registration must leave the original password and permissions intact.
    await page.getByRole("button", { name: "注册新账号", exact: true }).click();
    await page
      .getByLabel("密码", { exact: true })
      .fill("Different-password-123");
    await page
      .getByLabel("确认密码", { exact: true })
      .fill("Different-password-123");
    await page.getByRole("button", { name: "注册账号", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("账号已存在");
    await page.getByRole("button", { name: "返回登录", exact: true }).click();
    await page.getByLabel("密码", { exact: true }).fill("Member-password-123");
    await page.getByRole("button", { name: /进入工作空间/ }).click();
    await expect(page.getByText(/当前账号尚未获得门店数据授权/)).toBeVisible();
    await page.reload();
    await expect(page.getByText(/当前账号尚未获得门店数据授权/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "经营总览", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "权限与配置", exact: true }).click();
    await expect(page.getByText(/账号：browser-member/)).toBeVisible();
    await expect(page.getByText("账号与门店授权", { exact: true })).toHaveCount(
      0,
    );
    await page.getByRole("button", { name: "退出登录", exact: true }).click();

    await page.getByLabel("账号", { exact: true }).fill("owner");
    await page.getByLabel("密码", { exact: true }).fill("Owner-password-123");
    await page.getByRole("button", { name: /进入工作空间/ }).click();
    await expect(page.getByText("首次使用：", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "数据中心", exact: true }).click();
    await page.getByRole("button", { name: /导入/ }).first().click();
    const inputs = page.locator('input[type=file][accept=".csv"]');
    for (const [i, table] of [
      "stores",
      "products",
      "orders",
      "items",
    ].entries())
      await inputs.nth(i).setInputFiles({
        name: table + ".csv",
        mimeType: "text/csv",
        buffer: Buffer.from(files[table]),
      });
    await page.getByLabel(/数据来源/).fill("浏览器集成验收夹具");
    await page.getByRole("button", { name: /执行质量校验/ }).click();
    await expect(
      page.getByText("校验通过", { exact: true }).first(),
    ).toBeVisible();
    await page.getByLabel(/我确认上述日期/).check();
    await page.getByRole("button", { name: /发布数据版本/ }).click();
    await expect(
      page.getByText("数据版本已发布", { exact: false }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "智能分析", exact: false })
      .first()
      .click();
    await page.getByLabel("经营分析问题").fill("最近如何");
    await page.getByRole("button", { name: /开始分析/ }).click();
    await expect(
      page.getByText("待澄清", { exact: true }).first(),
    ).toBeVisible();
    await page
      .getByLabel("经营分析问题")
      .fill("2026 年 8 月订单数和客单价相比 7 月变化多少？");
    await page.getByRole("button", { name: /开始分析/ }).click();
    await expect(
      page.getByText("已完成", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("有效订单数", { exact: true }).first(),
    ).toBeVisible();
    const taskUrl = page.url();
    expect(taskUrl).toContain("task=");
    await page.reload();
    await expect(
      page.getByText("已完成", { exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("button", { name: /生成报告/ }).click();
    await page.getByRole("button", { name: /打开/ }).first().click();
    await page.getByRole("button", { name: /提交审核/ }).click();
    await page.getByPlaceholder(/请说明核验/).fill("核心指标及证据已核对");
    await page.getByRole("button", { name: /审核通过/ }).click();
    await expect(
      page.getByText("已通过", { exact: true }).first(),
    ).toBeVisible();
    expect(page.url()).toContain("report=");
    await page.reload();
    await expect(
      page.getByRole("link", { name: "打印 / 保存 PDF" }),
    ).toBeVisible();
    await page.getByLabel("跟进建议").selectOption({ label: "整份报告" });
    await page.getByLabel("到期日期").fill("2026-10-10");
    await page.getByLabel("复核指标与结果").fill("下月净销量复核");
    await page.getByLabel("计划 / 已执行行动").fill("核对门店记录");
    await page.getByRole("button", { name: "保存反馈", exact: true }).click();
    await expect(page.getByText("行动：核对门店记录")).toBeVisible();
    await page.screenshot({
      path: "test-results/report-page.png",
      fullPage: true,
    });
    const popupPromise = page.waitForEvent("popup");
    await page.getByRole("link", { name: "打印 / 保存 PDF" }).click();
    const popup = await popupPromise;
    await expect(
      popup.getByText("指标版本：sales-v1", { exact: false }),
    ).toBeVisible();
    mkdirSync("test-results", { recursive: true });
    await popup.pdf({
      path: "test-results/report.pdf",
      format: "A4",
      printBackground: true,
    });
    await popup.close();
    const originalReportUrl = page.url();
    await page.getByRole("button", { name: /创建新版本|创建修订/ }).click();
    await expect.poll(() => page.url()).not.toBe(originalReportUrl);
    await page.getByLabel("业务说明", { exact: true }).fill("未保存修改");
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "继续编辑", exact: true }).click();
    await expect(page.getByLabel("业务说明", { exact: true })).toHaveValue(
      "未保存修改",
    );
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page
      .getByRole("button", { name: "放弃修改并关闭", exact: true })
      .click();
    await page.getByRole("button", { name: "经营总览", exact: true }).click();
    await page.getByLabel("结束日期").fill("2026-08-30");
    await page.getByRole("button", { name: /深入分析经营变化/ }).click();
    await expect(
      page.getByText("当前筛选不是完整自然月", { exact: false }),
    ).toBeVisible();
    await page.getByLabel("结束日期").fill("2026-08-31");
    await page.getByLabel("门店筛选").selectOption("SH01");
    await page.getByRole("button", { name: /深入分析经营变化/ }).click();
    await expect(page.getByLabel("经营分析问题")).toHaveValue(
      "2026 年 8 月上海静安店净销售额是多少？",
    );

    await page.getByRole("button", { name: "权限与配置", exact: true }).click();
    await page.getByRole("button", { name: "退出登录", exact: true }).click();
    await expect(page.getByLabel("账号", { exact: true })).toBeVisible();
    await expect(page.getByText("会话已过期", { exact: false })).toHaveCount(0);
    await page.getByLabel("账号", { exact: true }).fill("owner");
    await page.getByLabel("密码", { exact: true }).fill("Owner-password-123");
    await page.getByRole("button", { name: /进入工作空间/ }).click();
    await expect(
      page.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    await page.waitForLoadState("networkidle");
    await call("POST", "/api/logout", "{}", {
      "X-Requested-With": "RetailPilot",
      "Content-Type": "application/json",
    });
    await page
      .getByRole("button", { name: "智能分析", exact: false })
      .first()
      .click();
    await expect(page.getByLabel("账号", { exact: true })).toBeVisible();
    await expect(page.getByText("会话已过期", { exact: false })).toBeVisible();
  } finally {
    bridge.stdin.end();
    bridge.kill();
  }
});
