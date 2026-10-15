// One command prepares and starts RetailPilot; the launcher owns all child processes.
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(root);
const production = process.argv.includes("--production");
const windows = process.platform === "win32";
const python = resolve(
  root,
  ".venv",
  windows ? "Scripts/python.exe" : "bin/python",
);
const npmCli = process.env.npm_execpath;
const children = new Set();
let stopping = false;

function signalChild(child, signal) {
  if (!child.pid) return;
  try {
    if (windows) {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
      });
    } else {
      process.kill(-child.pid, signal);
    }
  } catch (error) {
    if (error.code !== "ESRCH") console.error("关闭服务失败：", error.message);
  }
}

async function stop(code) {
  if (stopping) return;
  stopping = true;
  console.log("\n正在关闭 RetailPilot…");
  const active = [...children];
  const exited = active.map(
    (child) =>
      new Promise((done) => {
        if (child.exitCode !== null || child.signalCode !== null) done();
        else child.once("exit", done);
      }),
  );
  active.forEach((child) => signalChild(child, "SIGTERM"));
  await Promise.race([Promise.all(exited), delay(5000)]);
  // Also clean up reload workers if their parent has already exited.
  active.forEach((child) => signalChild(child, "SIGKILL"));
  process.exit(code);
}

process.on("SIGINT", () => void stop(0));
process.on("SIGTERM", () => void stop(0));

function launch(command, args, label, service = false) {
  if (stopping) throw new Error("启动已取消。");
  const child = spawn(command, args, {
    cwd: root,
    env: process.env,
    stdio: "inherit",
    detached: !windows,
  });
  children.add(child);
  const done = new Promise((accept, reject) => {
    child.once("error", (error) =>
      reject(new Error(`${label}：${error.message}`)),
    );
    child.once("exit", (code, signal) => {
      if (!service) children.delete(child);
      if (code === 0) accept();
      else
        reject(
          new Error(`${label}退出（${signal || code}），请检查上方日志。`),
        );
    });
  });
  if (service) {
    done.then(
      () => {
        if (!stopping) {
          console.error(`${label}已停止，关闭其他服务。`);
          void stop(1);
        }
      },
      (error) => {
        if (!stopping) {
          console.error(error.message);
          void stop(1);
        }
      },
    );
  }
  return done;
}

function npm(args, label) {
  if (!npmCli) throw new Error("请通过 npm run dev 或 npm start 启动项目。");
  return launch(process.execPath, [npmCli, ...args], label);
}

async function checkPort(port) {
  await new Promise((accept, reject) => {
    const server = createServer();
    server.once("error", (error) =>
      reject(
        new Error(
          `端口 ${port} 不可用（${error.code}）。请先停止占用该端口的服务，再重新启动。`,
        ),
      ),
    );
    server.listen(port, "127.0.0.1", () => server.close(accept));
  });
}

async function ready(url) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && !stopping) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      await response.arrayBuffer();
      if (response.ok) return;
    } catch {
      // Wait for the server to finish startup.
    }
    await delay(250);
  }
  throw new Error(`服务启动超时：${url}，请检查上方日志。`);
}

try {
  if (Number(process.versions.node.split(".")[0]) < 22)
    throw new Error("需要 Node.js 22 或更高版本。");
  await checkPort(8000);
  if (!production) await checkPort(5173);

  const require = createRequire(import.meta.url);
  try {
    for (const dependency of [
      "vite",
      "typescript",
      "@vitejs/plugin-react",
      "react",
      "react-dom",
      "recharts",
      "lucide-react",
    ])
      require.resolve(dependency);
  } catch {
    console.log("首次启动：正在安装前端依赖…");
    await npm(["ci"], "前端依赖安装");
  }
  const probe = existsSync(python)
    ? spawnSync(
        python,
        [
          "-c",
          "import sys, fastapi, uvicorn, httpx, pydantic, cryptography; assert sys.version_info >= (3, 11)",
        ],
        { stdio: "ignore" },
      )
    : null;
  if (probe?.status !== 0) {
    if (spawnSync("uv", ["--version"], { stdio: "ignore" }).status !== 0)
      throw new Error("请先安装 Python 3.11+ 和 uv，然后重新运行启动命令。");
    console.log("首次启动：正在准备 Python 环境和后端依赖…");
    await launch("uv", ["sync", "--frozen"], "后端依赖安装");
  }

  await launch(
    python,
    [
      "-m",
      "backend.manage",
      "init-admin",
      "--username",
      "owner",
      "--name",
      "系统负责人",
      "--if-empty",
    ],
    "管理员初始化",
  );
  if (production) await npm(["run", "build"], "前端构建");

  const backendArgs = [
    "-m",
    "uvicorn",
    "backend.app:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ];
  if (!production) backendArgs.push("--reload", "--reload-dir", "backend");
  launch(python, backendArgs, "后端服务", true);
  await ready("http://127.0.0.1:8000/api/health");
  if (!production) {
    launch(
      process.execPath,
      [
        resolve(root, "node_modules/vite/bin/vite.js"),
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--strictPort",
      ],
      "前端服务",
      true,
    );
    await ready("http://127.0.0.1:5173/");
  }
  console.log(
    `\nRetailPilot 已启动：\n  页面：http://127.0.0.1:${production ? 8000 : 5173}\n  API 文档：http://127.0.0.1:8000/docs\n  按 Ctrl+C 同时关闭所有服务。\n`,
  );
} catch (error) {
  if (!stopping) {
    console.error(`\n启动失败：${error.message}`);
    await stop(1);
  }
}
