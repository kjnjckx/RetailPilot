import { test, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

test("administrator configures, tests, persists and clears a model in the webpage", async ({
  page,
}) => {
  const bridge = spawn(
    ".venv/bin/python",
    ["-m", "scripts.browser_api_bridge"],
    {
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        RETAILPILOT_MODEL_PROVIDER: "rules",
        RETAILPILOT_MODEL_API_KEY: "",
        RETAILPILOT_MODEL_NAME: "",
        RETAILPILOT_BROWSER_MOCK_MODEL: "1",
      },
    },
  );
  const pending = new Map<number, (value: any) => void>();
  let counter = 0;
  let readyResolve: (value: any) => void;
  let readyReject: (reason: Error) => void;
  const ready = new Promise<any>((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  createInterface({ input: bridge.stdout }).on("line", (line) => {
    const value = JSON.parse(line);
    if (value.ready) readyResolve(value);
    else {
      pending.get(value.id)?.(value);
      pending.delete(value.id);
    }
  });
  let errorOutput = "";
  bridge.stderr.on("data", (chunk) => (errorOutput += chunk.toString()));
  bridge.on("exit", (code) => {
    if (code) readyReject(new Error(errorOutput || "Test bridge exited"));
  });
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
    await ready;
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
    await page.getByLabel("账号", { exact: true }).fill("owner");
    await page.getByLabel("密码", { exact: true }).fill("Owner-password-123");
    await page.getByRole("button", { name: /进入工作空间/ }).click();
    await page.getByRole("button", { name: "权限与配置", exact: true }).click();
    const panel = page.locator("section").filter({
      has: page.getByRole("heading", { name: "模型配置", exact: true }),
    });
    await expect(panel).toBeVisible();
    await panel
      .getByLabel("运行模式", { exact: true })
      .selectOption("openai-compatible");
    await panel
      .getByLabel("服务地址", { exact: true })
      .fill("https://api.openai.com/v1");
    await panel.getByLabel("模型名称", { exact: true }).fill("browser-model");
    await panel
      .getByLabel("API Key", { exact: true })
      .fill("browser-test-secret");
    await panel.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(panel.getByText(/测试成功/)).toBeVisible();
    const initial = await call("GET", "/api/model-status");
    expect(
      JSON.parse(Buffer.from(initial.body, "base64").toString()).enabled,
    ).toBe(false);
    await panel.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(panel.getByText(/已保存，大模型协作已启用/)).toBeVisible();
    await expect(panel.getByLabel("API Key", { exact: true })).toHaveValue("");
    await expect(panel.getByText(/已保存密钥/).first()).toBeVisible();
    await page.reload();
    await expect(panel.getByLabel("运行模式", { exact: true })).toHaveValue(
      "openai-compatible",
    );
    await expect(panel.getByLabel("模型名称", { exact: true })).toHaveValue(
      "browser-model",
    );
    await expect(panel.getByLabel("API Key", { exact: true })).toHaveValue("");
    expect(await page.locator("body").innerText()).not.toContain(
      "browser-test-secret",
    );
    await panel
      .getByLabel("模型名称", { exact: true })
      .fill("unavailable-model");
    await panel.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(panel.getByText(/失败|超时|不合规/)).toBeVisible();
    const stored = await call("GET", "/api/model-config");
    expect(
      JSON.parse(Buffer.from(stored.body, "base64").toString()).model_name,
    ).toBe("browser-model");
    await panel.getByLabel("运行模式", { exact: true }).selectOption("rules");
    await panel.getByLabel("清除已保存密钥", { exact: true }).check();
    await panel.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(panel.getByText(/已保存，当前使用规则模式/)).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await panel.scrollIntoViewIfNeeded();
    await expect
      .poll(() =>
        page
          .locator(".sidebar")
          .evaluate((element) => element.getBoundingClientRect().right),
      )
      .toBeLessThanOrEqual(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    mkdirSync("test-results", { recursive: true });
    await panel.screenshot({
      path: "test-results/model-config-mobile.png",
      animations: "disabled",
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await panel.scrollIntoViewIfNeeded();
    await panel.screenshot({
      path: "test-results/model-config-desktop.png",
      animations: "disabled",
    });

    const created = await call(
      "POST",
      "/api/users",
      JSON.stringify({
        id: "staff",
        name: "普通人员",
        password: "Staff-password-123",
        roles: ["store"],
        stores: [],
      }),
      { "X-Requested-With": "RetailPilot", "Content-Type": "application/json" },
    );
    expect(created.status).toBe(200);
    await page.getByRole("button", { name: "退出登录", exact: true }).click();
    await page.getByLabel("账号", { exact: true }).fill("staff");
    await page.getByLabel("密码", { exact: true }).fill("Staff-password-123");
    await page.getByRole("button", { name: /进入工作空间/ }).click();
    await page.getByRole("button", { name: "权限与配置", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "模型配置", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "确定性规则工作流", exact: true }),
    ).toBeVisible();
  } finally {
    bridge.stdin.end();
    bridge.kill();
  }
});
