import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Bell,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Database,
  FileText,
  LayoutDashboard,
  Menu,
  MoreHorizontal,
  Plus,
  Settings2,
  Sparkles,
  Store,
  UploadCloud,
  X,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { Obj, api, readLocation, writeLocation, User, Version } from "./lib";
import { Modal } from "./components/ui";

import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import DataCenter from "./pages/DataCenter";
import Analysis from "./pages/Analysis";
import Reports from "./pages/Reports";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";

export default function App() {
  const [user, setUser] = useState<User | null>(null),
    [boot, setBoot] = useState(true),
    [page, setPage] = useState(readLocation("page") || "overview"),
    [versions, setVersions] = useState<Version[]>([]),
    [version, setVersion] = useState(readLocation("version")),
    [toast, setToast] = useState(""),
    [mobile, setMobile] = useState(false),
    [refresh, setRefresh] = useState(0),
    [help, setHelp] = useState(false),
    [sessionNotice, setSessionNotice] = useState(""),
    [initialQ, setInitialQ] = useState("");
  const sessionActive = useRef(false);
  useEffect(() => {
    const expired = () => {
      if (sessionActive.current) {
        sessionActive.current = false;
        setSessionNotice("会话已过期，请重新登录");
      }
      setUser(null);
    };
    window.addEventListener("retailpilot:unauthorized", expired);
    return () =>
      window.removeEventListener("retailpilot:unauthorized", expired);
  }, []);
  useEffect(() => {
    api("/me")
      .then((u) => {
        sessionActive.current = true;
        setUser(u);
      })
      .catch(() => {})
      .finally(() => setBoot(false));
  }, []);
  useEffect(() => {
    if (user)
      api("/versions")
        .then((v) => {
          setVersions(v);
          setVersion((old) =>
            v.some((x: Obj) => x.id === old) ? old : v[0]?.id || "",
          );
        })
        .catch((e) => notify(e.message));
  }, [user, refresh]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    const restore = () => {
      setPage(readLocation("page") || "overview");
      setVersion(readLocation("version"));
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (version) writeLocation({ version });
  }, [version]);
  function notify(s: string) {
    setToast(s);
  }
  function go(p: string) {
    if (
      !window.dispatchEvent(
        new Event("retailpilot:before-navigate", { cancelable: true }),
      )
    )
      return;
    writeLocation({ page: p, task: "", report: "" }, true);
    setPage(p);
    setMobile(false);
  }
  function selectVersion(id: string) {
    writeLocation({ version: id, start: "", end: "", store: "" });
    setVersion(id);
  }
  function ask(q: string) {
    setInitialQ(q);
    go("analysis");
  }
  if (boot)
    return (
      <div className="boot">
        <Loader2 className="spin" /> 正在连接零售智航…
      </div>
    );
  if (!user)
    return (
      <Login
        notice={sessionNotice}
        onLogin={(u) => {
          sessionActive.current = true;
          setSessionNotice("");
          setUser(u as User);
          setPage(u.roles.includes("admin") ? "settings" : "overview");
        }}
      />
    );
  const nav = [
    ["overview", LayoutDashboard, "经营总览"],
    ["data", Database, "数据中心"],
    ["analysis", Sparkles, "智能分析"],
    ["reports", FileText, "报告中心"],
  ] as const;
  const titles: Obj = {
    overview: "经营总览",
    data: "数据中心",
    analysis: "智能分析",
    reports: "报告中心",
    settings: "权限与配置",
    audit: "操作日志",
  };
  const business = user.roles.some((r) =>
    ["analyst", "manager", "store"].includes(r),
  );
  const importer = user.roles.some((r) => ["analyst", "admin"].includes(r));
  const pageAllowed =
    page === "settings" ||
    page === "audit" ||
    (page === "data" ? importer : business);
  const current = versions.find((v) => v.id === version);
  return (
    <div className="app">
      <aside className={"sidebar " + (mobile ? "open" : "")}>
        <a className="brand" onClick={() => go("overview")}>
          <span className="brand-symbol">
            <BarChart3 size={25} />
          </span>
          <span>
            RetailPilot<small>零售智航</small>
          </span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">
            <Store size={18} />
          </span>
          <div>
            零售经营工作空间<small>单企业 · 本地工作空间</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <div className="nav-label">工作空间</div>
        <nav>
          {nav
            .filter(([id]) => (id === "data" ? importer : business))
            .map(([id, Icon, label]) => (
              <button
                key={id}
                className={page === id ? "active" : ""}
                onClick={() => go(id)}
              >
                <Icon size={19} />
                {label}
                {id === "analysis" && <span className="tiny-tag">LAB</span>}
              </button>
            ))}
        </nav>
        <div className="nav-label system-label">系统管理</div>
        <nav>
          <button
            className={page === "audit" ? "active" : ""}
            onClick={() => go("audit")}
          >
            <Activity size={19} />
            操作日志
          </button>
          <button
            className={page === "settings" ? "active" : ""}
            onClick={() => go("settings")}
          >
            <Settings2 size={19} />
            权限与配置
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="help-card">
            <div>
              <span className="live-dot" /> 数据有据，决策有底
            </div>
            <p>每一项洞察，都能回到数据本身。</p>
            <button onClick={() => setHelp(true)}>
              了解工作流程 <ArrowUpRight size={15} />
            </button>
          </div>
          <button className="profile" onClick={() => go("settings")}>
            <span className="avatar">{user.name[0]}</span>
            <span>
              {user.name}
              <small>
                {user.roles.includes("manager")
                  ? "经营管理人员"
                  : user.roles.includes("admin")
                    ? "系统管理员"
                    : "门店业务人员"}
              </small>
            </span>
            <MoreHorizontal size={18} />
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu icon-btn"
              onClick={() => setMobile(!mobile)}
              aria-label="打开菜单"
            >
              <Menu size={20} />
            </button>
            <span>工作空间</span>
            <ChevronRight size={13} />
            <b>{titles[page]}</b>
          </div>
          <div className="top-actions">
            <span className="environment">
              <span className="live-dot" />
              本地运行
            </span>
            <span className="divider" />
            <button
              className="icon-btn"
              title="使用帮助"
              onClick={() => setHelp(true)}
            >
              <CircleHelp size={19} />
            </button>
            <button
              className="icon-btn"
              title="查看操作日志"
              onClick={() => go("audit")}
            >
              <Bell size={19} />
            </button>
            <span className="avatar small">{user.name[0]}</span>
          </div>
        </header>
        <main>
          <div className="page-header">
            <div>
              <div className="eyebrow">RETAIL INTELLIGENCE WORKSPACE</div>
              <h1>
                {titles[page]}
                <span className="heading-dot">.</span>
              </h1>
              <p>
                {
                  (
                    {
                      overview: "看见每一份变化，把经营数据变成下一步行动。",
                      data: "从可信的数据开始，为每一次分析建立可靠基础。",
                      analysis: "提出你的经营问题，沿着证据找到答案。",
                      reports: "沉淀分析发现，让决策从洞察走向行动。",
                      audit: "关键操作留痕，让业务过程清晰可追溯。",
                      settings: "管理模型服务、角色与数据可见范围。",
                    } as Record<string, string>
                  )[page]
                }
              </p>
            </div>
            <div className="header-actions">
              {pageAllowed && page === "overview" && (
                <>
                  <button
                    className="btn"
                    disabled={!importer}
                    onClick={() => go("data")}
                  >
                    <UploadCloud size={16} />
                    导入数据
                  </button>
                  <button className="btn primary" onClick={() => ask("")}>
                    <Sparkles size={16} />
                    发起智能分析
                  </button>
                </>
              )}
              {pageAllowed && page === "reports" && (
                <button className="btn primary" onClick={() => ask("")}>
                  <Plus size={16} />
                  新建分析报告
                </button>
              )}
            </div>
          </div>
          {!pageAllowed && (
            <div className="notice">
              当前角色没有此页面权限，请通过左侧菜单选择可用页面。
            </div>
          )}
          {!versions.length && (
            <div className="notice">
              {user.roles.includes("admin")
                ? "首次使用：① 创建或接收注册账号 ② 数据中心导入并发布 ③ 权限与配置授权门店 ④ 智能分析生成首份报告。"
                : !user.stores.length
                  ? "当前账号尚未获得门店数据授权，请联系管理员在“权限与配置”中分配门店和业务角色。授权后重新登录即可使用。"
                  : "暂无可访问的已发布数据，请联系管理员导入并发布经营数据。"}
              <button
                className="btn"
                onClick={() => go(importer ? "data" : "settings")}
              >
                继续设置
              </button>
            </div>
          )}
          {pageAllowed && page === "overview" && (
            <Dashboard
              version={version}
              versions={versions}
              setVersion={selectVersion}
              ask={ask}
              notify={notify}
            />
          )}
          {pageAllowed && page === "data" && (
            <DataCenter
              user={user}
              versions={versions}
              onRefresh={() => setRefresh((x) => x + 1)}
              notify={notify}
            />
          )}
          {pageAllowed && page === "analysis" && (
            <Analysis
              version={version}
              versions={versions}
              setVersion={selectVersion}
              initialQ={initialQ}
              clearQ={() => setInitialQ("")}
              user={user}
              notify={notify}
              onReport={() => go("reports")}
            />
          )}
          {pageAllowed && page === "reports" && (
            <Reports user={user} notify={notify} />
          )}
          {page === "audit" && <Audit notify={notify} />}
          {page === "settings" && (
            <Settings
              user={user}
              notify={notify}
              onUserUpdated={async () => {
                setUser(await api("/me"));
                setRefresh((x) => x + 1);
              }}
              logout={async () => {
                try {
                  await api("/logout", {});
                  sessionActive.current = false;
                  setSessionNotice("");
                  setUser(null);
                } catch (e: any) {
                  notify(e.message);
                }
              }}
            />
          )}
          <footer>
            <span>
              <span className="live-dot" /> RetailPilot · 零售智航
            </span>
            <span>
              {current?.id || "暂无数据版本"}
              {current?.source && (
                <>
                  <span className="footer-sep">/</span>
                  {current.source}
                </>
              )}
              <span className="footer-sep">/</span>v0.1
            </span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <AlertCircle size={18} />
          {toast}
          <button className="icon-btn" onClick={() => setToast("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {help && (
        <Modal title="从数据到决策，四步开始" onClose={() => setHelp(false)}>
          <div className="help-steps">
            {[
              [
                "01",
                "准备数据",
                "下载四张 CSV 模板，上传后完成字段映射、校验和发布。",
              ],
              [
                "02",
                "看见变化",
                "在经营总览选择日期、门店和数据版本，查看统一口径的指标。",
              ],
              [
                "03",
                "沿证据分析",
                "输入含年份、月份和指标的问题。规则工作流精确计算；管理员可在权限与配置启用大模型，复核计划并补充后续建议。",
              ],
              [
                "04",
                "形成决策",
                "生成报告草稿，提交并审核后导出 Markdown，记录建议采纳反馈。",
              ],
            ].map(([n, t, d]) => (
              <div key={n}>
                <span>{n}</span>
                <section>
                  <h3>{t}</h3>
                  <p>{d}</p>
                </section>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
