import React, { useEffect, useState, useRef } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronRight,
  Database,
  FileText,
  ShieldCheck,
  Sparkles,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
} from "lucide-react";
import {
  Obj,
  Task,
  TaskSummary,
  User,
  Version,
  when,
  api,
  latestCompleteMonth,
  readLocation,
  writeLocation,
} from "../lib";
import MetricResults from "../components/MetricResults";
import { Modal, Badge, Empty } from "../components/ui";

export default function Analysis({
  version,
  versions,
  setVersion,
  initialQ,
  clearQ,
  user,
  notify,
  onReport,
}: {
  version: string;
  versions: Version[];
  setVersion: (s: string) => void;
  initialQ: string;
  clearQ: () => void;
  user: User;
  notify: (s: string) => void;
  onReport: () => void;
}) {
  const [question, setQuestion] = useState(initialQ),
    [tasks, setTasks] = useState<TaskSummary[]>([]),
    [task, setTask] = useState<Task | null>(null),
    [busy, setBusy] = useState(false),
    [evidence, setEvidence] = useState(false),
    [page, setPage] = useState(1),
    [pages, setPages] = useState(1),
    [engine, setEngine] = useState("正在读取运行模式…");
  const selection = useRef(0);
  const openTask = async (id: string) => {
    const token = ++selection.current;
    setTask(null);
    if (!id) return;
    try {
      const t = await api("/tasks/" + id);
      if (token === selection.current) {
        setTask(t);
        setQuestion(t.question);
      }
    } catch (e: any) {
      if (token === selection.current) notify(e.message);
    }
  };
  const reload = () =>
    api("/tasks?page=" + page)
      .then((r) => {
        setTasks(r.items);
        setPages(r.pages);
      })
      .catch((e) => notify(e.message));
  useEffect(() => {
    api("/model-status")
      .then((r) => setEngine(r.engine))
      .catch((e) => {
        setEngine("运行模式读取失败");
        notify(e.message);
      });
    const id = readLocation("task");
    if (id) openTask(id);
    clearQ();
  }, []);
  useEffect(() => {
    const restore = () => openTask(readLocation("task"));
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    reload();
  }, [page]);
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    let alive = true;
    const controller = new AbortController();
    const timer = setInterval(
      () =>
        api("/tasks/" + task.id, undefined, controller.signal)
          .then((t) => {
            if (!alive) return;
            setTask(t);
            if (!["queued", "running"].includes(t.status)) reload();
          })
          .catch((e) => {
            if (!alive) return;
            notify(e.message);
            clearInterval(timer);
          }),
      700,
    );
    return () => {
      alive = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [task?.id, task?.status]);
  useEffect(() => {
    if (task) writeLocation({ task: task.id });
  }, [task?.id]);
  async function run() {
    setBusy(true);
    try {
      const t = await api("/tasks", {
        question,
        version_id: version,
        ...(task &&
        ["failed", "cancelled", "clarification"].includes(task.status)
          ? { retry_of: task.id }
          : {}),
      });
      setTask(t);
      reload();
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }
  const selected = versions.find((v) => v.id === version);
  const month = selected
    ? latestCompleteMonth(selected.coverage_start, selected.coverage_end)
    : "";
  const examples = month
    ? [
        `${month}净销售额是多少？`,
        `${month}各门店净销售额排名如何？`,
        `${month}有效订单数和客单价是多少？`,
      ]
    : [];
  return (
    <div className="analysis-layout">
      <div className="analysis-main">
        <section className="panel ask-panel">
          <div className="ask-label">
            <span>
              <Sparkles size={20} />
              今天，想从数据中了解什么？
            </span>
            <span className="subtle-tag">{engine}</span>
          </div>
          <textarea
            aria-label="经营分析问题"
            placeholder={
              month
                ? `例如：${month}净销售额是多少？`
                : "请先发布覆盖完整自然月的数据，再输入经营问题"
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                if (
                  !busy &&
                  !["queued", "running"].includes(task?.status || "")
                )
                  run();
              }
            }}
          />
          <div className="ask-controls">
            <div>
              <Database size={15} />
              <select
                aria-label="分析数据版本"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="btn primary"
              disabled={
                busy ||
                !question.trim() ||
                !version ||
                ["queued", "running"].includes(task?.status || "")
              }
              onClick={run}
            >
              {busy ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <Sparkles size={16} />
              )}
              开始分析 <ArrowRight size={15} />
            </button>
          </div>
        </section>
        {!task && (
          <>
            <div className="suggestions">
              {examples.map((q, i) => (
                <button key={q} onClick={() => setQuestion(q)}>
                  <span>0{i + 1}</span>
                  {q}
                  <ArrowUpRight size={16} />
                </button>
              ))}
            </div>
            <section className="analysis-intro">
              <div className="analysis-intro-symbol">
                <Sparkles size={32} />
              </div>
              <h2>不止于答案，更有可复核的依据。</h2>
              <p>
                规划、查询、统计、检查、编制，每一步都记录输入与结果。
                <br />
                所有数值由程序计算，解释假设与数据事实明确区分。
              </p>
              <div className="intro-features">
                <span>
                  <ShieldCheck size={17} />
                  授权范围内分析
                </span>
                <span>
                  <Database size={17} />
                  固定数据快照
                </span>
                <span>
                  <CheckCheck size={17} />
                  核心数值核验
                </span>
              </div>
            </section>
          </>
        )}
        {task?.status === "queued" && (
          <div className="notice">
            任务已保存并等待执行。全工作空间最多同时执行两个任务，你可以取消排队任务。
          </div>
        )}
        {task && (
          <section className="panel task-panel">
            <div className="panel-heading">
              <div>
                <h2>
                  分析任务 <Badge status={task.status} />
                </h2>
                <p>
                  {task.id} · {task.version_id} · {when(task.at)}
                </p>
              </div>
              {["running", "clarification"].includes(task.status) &&
                task.user_id === user.id && (
                  <button
                    className="btn small-btn"
                    onClick={async () => {
                      try {
                        setTask(await api("/tasks/" + task.id + "/cancel", {}));
                        reload();
                      } catch (e: any) {
                        notify(e.message);
                      }
                    }}
                  >
                    取消任务
                  </button>
                )}
            </div>
            <div className="task-question">{task.question}</div>
            {task.plan && (
              <div className="scope-strip">
                <CalendarDays size={15} />
                {task.plan.start} — {task.plan.end}
                <span>·</span>
                {task.plan.region || "授权门店"} {task.plan.store}
                <span>·</span>
                {task.plan.metrics
                  ?.map(
                    (metric: string) =>
                      (
                        ({
                          net: "净销售额",
                          orders: "有效订单数",
                          average: "客单价",
                          quantity: "净销量",
                        }) as Record<string, string>
                      )[metric],
                  )
                  .join("、") || "净销售额"}
                <span>·</span>
                {task.plan.compare
                  ? `${task.plan.comparison === "yoy" ? "同比" : task.plan.comparison === "mom" ? "环比" : "对比"} ${task.plan.previous_start} — ${task.plan.previous_end}`
                  : "单期分析"}
              </div>
            )}
            <div className="workflow">
              {["规划", "查询", "统计分析", "结果检查", "报告编制"].map(
                (r, i) => (
                  <div
                    className={
                      task.steps[i]?.status === "completed"
                        ? "done"
                        : task.steps[i]?.status === "running"
                          ? "working"
                          : ""
                    }
                    key={r}
                  >
                    <span>
                      {task.steps[i]?.status === "completed" ? (
                        <Check size={14} />
                      ) : task.steps[i]?.status === "running" ? (
                        <Loader2 size={14} className="spin" />
                      ) : (
                        i + 1
                      )}
                    </span>
                    <b>{r}</b>
                    <small>
                      {task.steps[i]?.duration_ms != null
                        ? `${task.steps[i].duration_ms} ms`
                        : task.steps[i]?.status === "failed"
                          ? "失败"
                          : ""}
                    </small>
                  </div>
                ),
              )}
            </div>
            {task.error && (
              <div className="notice danger">
                <AlertCircle size={17} />
                {task.error}
              </div>
            )}
            {["failed", "cancelled", "clarification"].includes(task.status) && (
              <div className="task-padding">
                <button
                  className="btn"
                  onClick={() => {
                    setQuestion(task.question);
                    notify(
                      "可在上方补充条件，再点击开始分析；会创建新的关联任务。",
                    );
                  }}
                >
                  补充问题 / 重新执行
                </button>
              </div>
            )}
            {task.status === "completed" && task.result && (
              <div className="analysis-result">
                <div className="result-heading">
                  <span>
                    <CheckCircle2 size={19} />
                    分析完成，核心数值检查通过
                  </span>
                  <button
                    className="text-btn"
                    onClick={() => setEvidence(true)}
                  >
                    查看证据 <ExternalLink size={14} />
                  </button>
                </div>
                <h3>数据事实</h3>
                <p className="result-summary">{task.result.summary}</p>
                <MetricResults result={task.result} charts />
                <div className="result-limit">
                  <AlertCircle size={18} />
                  <div>
                    <h3>解释边界与待核实事项</h3>
                    <p>{task.result.limits}</p>
                  </div>
                </div>
                <h3>建议下一步</h3>
                <p>{task.result.recommendation}</p>
                {(user.roles.includes("analyst") ||
                  user.roles.includes("manager")) && (
                  <button
                    className="btn primary"
                    disabled={busy || task.result.current.empty}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api("/reports", { task_id: task.id });
                        notify("报告草稿已生成，可在报告中心审核。");
                        onReport();
                      } catch (e: any) {
                        notify(e.message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <FileText size={16} />
                    生成报告草稿 <ArrowRight size={15} />
                  </button>
                )}
              </div>
            )}
            {task.steps.length > 0 && (
              <details className="step-details">
                <summary>查看阶段输入与输出（{task.steps.length}）</summary>
                {task.steps.map((s: Obj, i: number) => (
                  <details key={i}>
                    <summary>
                      <Check size={14} />
                      {s.role} · {when(s.at)}
                    </summary>
                    <pre>
                      {JSON.stringify(
                        { input: s.input, output: s.output },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                ))}
              </details>
            )}
          </section>
        )}
      </div>
      <aside className="panel task-history">
        <div className="panel-heading">
          <h2>分析历史</h2>
          <span className="subtle-tag">{tasks.length}</span>
        </div>
        {!tasks.length ? (
          <Empty text="暂无分析" hint="你的探索将保存在这里。" />
        ) : (
          tasks.map((t) => (
            <button
              className={
                "history-item " + (task?.id === t.id ? "selected" : "")
              }
              onClick={() => {
                openTask(t.id);
                setQuestion(t.question);
              }}
              key={t.id}
            >
              <div>
                <Badge status={t.status} />
                <small>{when(t.at)}</small>
              </div>
              <p>{t.question}</p>
              <span>
                {t.version_id}
                <ChevronRight size={13} />
              </span>
            </button>
          ))
        )}
        <div className="pagination">
          <button
            className="btn"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <span>
            {page} / {pages}
          </span>
          <button
            className="btn"
            disabled={page >= pages}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
      </aside>
      {evidence && task?.result && (
        <Modal
          wide
          title="查询证据与数据明细"
          onClose={() => setEvidence(false)}
        >
          <div className="notice">
            {task.result.evidence.id} · {task.result.evidence.metric_version} ·
            金额单位：人民币分（界面展示已转换为元）
          </div>
          <pre className="evidence-json">
            {JSON.stringify(task.result.evidence, null, 2)}
          </pre>
        </Modal>
      )}
    </div>
  );
}
