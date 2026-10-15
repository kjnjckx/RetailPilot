import React, { useEffect, useState, useRef } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Download,
  FileText,
  Plus,
  Search,
  CheckCircle2,
} from "lucide-react";
import {
  Obj,
  Report,
  ReportSummary,
  User,
  when,
  statusLabel,
  api,
  readLocation,
  writeLocation,
} from "../lib";
import MetricResults from "../components/MetricResults";
import { Modal, Badge, Empty } from "../components/ui";

export default function Reports({
  user,
  notify,
}: {
  user: User;
  notify: (s: string) => void;
}) {
  const [reports, setReports] = useState<ReportSummary[]>([]),
    [selected, setSelected] = useState<Report | null>(null),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [busy, setBusy] = useState(false),
    [comment, setComment] = useState(""),
    [feedback, setFeedback] = useState("待评估"),
    [owner, setOwner] = useState(""),
    [saved, setSaved] = useState(""),
    [discard, setDiscard] = useState(false),
    [page, setPage] = useState(1),
    [pages, setPages] = useState(1),
    [total, setTotal] = useState(0),
    [counts, setCounts] = useState<Record<string, number>>({}),
    [suggestion, setSuggestion] = useState(""),
    [due, setDue] = useState(""),
    [actionText, setActionText] = useState(""),
    [reviewMetric, setReviewMetric] = useState("");
  const selection = useRef(0);
  const signature = (r: Obj) =>
    JSON.stringify([r.note, r.recommendation, r.title]);
  const openReport = async (r: Obj) => {
    const token = ++selection.current;
    try {
      const full = await api("/reports/" + r.id);
      if (token !== selection.current) return;
      setSelected(full);
      setSaved(signature(full));
      writeLocation({ report: r.id });
    } catch (e: any) {
      notify(e.message);
    }
  };
  const dirty = !!selected && saved !== signature(selected);
  const closeReport = (force = false) => {
    if (dirty && !force) {
      setDiscard(true);
      return;
    }
    setDiscard(false);
    ++selection.current;
    setSelected(null);
    writeLocation({ report: "" });
  };
  const reload = () =>
    api(
      "/reports?" +
        new URLSearchParams({
          page: String(page),
          q: search,
          status: filter === "all" ? "" : filter,
        }),
    )
      .then((r) => {
        setReports(r.items);
        setPages(r.pages);
        setTotal(r.total);
        setCounts(r.status_counts || {});
      })
      .catch((e) => notify(e.message));
  useEffect(() => {
    const id = readLocation("report");
    if (id) openReport({ id });
  }, []);
  useEffect(() => {
    const restore = () => {
      const id = readLocation("report");
      if (id) openReport({ id });
      else setSelected(null);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    api(
      "/reports?" +
        new URLSearchParams({
          page: String(page),
          q: search,
          status: filter === "all" ? "" : filter,
        }),
      undefined,
      controller.signal,
    )
      .then((r) => {
        if (alive) {
          setReports(r.items);
          setPages(r.pages);
          setTotal(r.total);
          setCounts(r.status_counts || {});
        }
      })
      .catch((e) => {
        if (alive) notify(e.message);
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [page, search, filter]);
  useEffect(() => {
    const guard = (e: Event) => {
      if (dirty && !window.confirm("有未保存的报告修改，仍然离开吗？"))
        e.preventDefault();
    };
    window.addEventListener("retailpilot:before-navigate", guard);
    return () =>
      window.removeEventListener("retailpilot:before-navigate", guard);
  }, [dirty]);
  async function act(action: string, body: Obj = {}) {
    if (!selected) return;
    setBusy(true);
    try {
      const r = await api(`/reports/${selected.id}/${action}`, body);
      setSelected(r);
      setSaved(signature(r));
      writeLocation({ report: r.id });
      setComment("");
      reload();
      notify("报告已更新");
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }
  const canEdit = user.roles.some((r: string) =>
      ["analyst", "manager"].includes(r),
    ),
    manager = user.roles.includes("manager");
  const list = reports.filter(
    (r) =>
      (filter === "all" || r.status === filter) &&
      (r.title + r.id).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="report-stats">
        {[
          ["all", "全部报告", Object.values(counts).reduce((n, v) => n + v, 0)],
          ["draft", "草稿", counts.draft || 0],
          ["pending", "待审核", counts.pending || 0],
          ["approved", "已通过", counts.approved || 0],
        ].map(([id, label, n]) => (
          <button
            key={id}
            className={"panel report-stat " + (filter === id ? "selected" : "")}
            onClick={() => {
              setFilter(String(id));
              setPage(1);
            }}
          >
            <span>{label}</span>
            <b>{n}</b>
            <ArrowUpRight size={19} />
          </button>
        ))}
      </div>
      <section className="panel">
        <div className="panel-heading">
          <h2>经营分析报告</h2>
          <div className="search-box">
            <Search size={16} />
            <input
              aria-label="搜索报告"
              placeholder="搜索报告名称或编号"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
        </div>
        {!list.length ? (
          <Empty
            text={reports.length ? "没有匹配的报告" : "让洞察成为可执行的决策"}
            hint={
              reports.length
                ? "试试其他关键词或状态。"
                : "完成一次智能分析后，生成你的第一份经营报告。"
            }
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>报告名称</th>
                  <th>数据版本</th>
                  <th>创建时间</th>
                  <th>状态</th>
                  <th>修订</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <div className="report-title-cell">
                        <span className="file-icon">
                          <FileText size={20} />
                        </span>
                        <div>
                          <b>{r.title}</b>
                          <small>
                            {r.id} · {r.creator}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td>{r.version_id}</td>
                    <td>{when(r.at)}</td>
                    <td>
                      <Badge status={r.status} />
                    </td>
                    <td>v{r.revision}</td>
                    <td>
                      <button
                        className="text-btn"
                        onClick={() => {
                          openReport(r);
                          setComment("");
                        }}
                      >
                        打开 <ArrowUpRight size={15} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="pagination">
        <button
          className="btn"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </button>
        <span>
          {page} / {pages} · {total} 份
        </span>
        <button
          className="btn"
          disabled={page >= pages}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </button>
      </div>
      {selected && (
        <Modal wide title="经营分析报告" onClose={() => closeReport()}>
          {discard && (
            <div className="notice" role="alert">
              有未保存的修改。
              <button className="btn" onClick={() => setDiscard(false)}>
                继续编辑
              </button>
              <button className="btn" onClick={() => closeReport(true)}>
                放弃修改并关闭
              </button>
            </div>
          )}
          <div className="report-document">
            <div className="report-document-head">
              <span>RETAILPILOT / BUSINESS INSIGHT</span>
              <Badge status={selected.status} />
            </div>
            <h2>{selected.title}</h2>
            <p className="muted">
              {selected.id} · 修订 v{selected.revision} · 数据版本{" "}
              {selected.version_id} · 指标版本{" "}
              {selected.metric_version ||
                selected.result.evidence.metric_version}
            </p>
            <hr />
            <h3>01 / 数据事实</h3>
            <p className="result-summary">{selected.summary}</p>
            <div className="scope-strip">
              证据 {selected.result.evidence.id} ·{" "}
              {selected.result.evidence.plan.start} —{" "}
              {selected.result.evidence.plan.end}
            </div>
            <MetricResults result={selected.result} />
            <h3>02 / 业务说明与建议</h3>
            {canEdit && ["draft", "rejected"].includes(selected.status) ? (
              <>
                <label>
                  业务说明
                  <textarea
                    aria-label="业务说明"
                    placeholder="补充业务背景与需核实的情况"
                    value={selected.note}
                    onChange={(e) =>
                      setSelected({ ...selected, note: e.target.value })
                    }
                  />
                </label>
                <label>
                  建议
                  <textarea
                    aria-label="建议"
                    value={selected.recommendation}
                    onChange={(e) =>
                      setSelected({
                        ...selected,
                        recommendation: e.target.value,
                      })
                    }
                  />
                </label>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() =>
                    act("edit", {
                      note: selected.note,
                      recommendation: selected.recommendation,
                    })
                  }
                >
                  保存正文修改
                </button>
              </>
            ) : (
              <>
                <p>{selected.note || "未补充业务说明。"}</p>
                <p>{selected.recommendation}</p>
              </>
            )}
            <h3>03 / 限制与证据</h3>
            <p>{selected.result.limits}</p>
            <details>
              <summary>查看完整查询证据</summary>
              <pre>{JSON.stringify(selected.result.evidence, null, 2)}</pre>
            </details>
            {selected.reviews.length > 0 && (
              <>
                <h3>审核记录</h3>
                {selected.reviews.map((r: Obj, i: number) => (
                  <div className="review-record" key={i}>
                    <CheckCircle2 size={17} />
                    <div>
                      <b>
                        {r.actor} · {statusLabel[r.decision]}
                      </b>
                      <p>{r.comment}</p>
                      <small>
                        {when(r.at)} · v{r.revision}
                      </small>
                    </div>
                  </div>
                ))}
              </>
            )}
            {selected.status === "pending" && manager && (
              <label>
                审核意见
                <textarea
                  placeholder="请说明核验结果或需要修改的内容"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </label>
            )}
            {selected.status === "approved" && (
              <>
                <h3>建议采纳与反馈</h3>
                {selected.feedback.map((f: Obj, i: number) => (
                  <div className="review-record" key={i}>
                    <CheckCircle2 size={16} />
                    <div>
                      <b>
                        {f.state} · {f.actor}
                      </b>
                      <p>
                        {f.owner && `负责人：${f.owner}；`}
                        {f.comment || "未填写说明"}
                      </p>
                      <p>
                        建议：{f.suggestion || "整份报告"} · 到期：
                        {f.due || "未设置"}
                      </p>
                      <p>行动：{f.action || "待记录"}</p>
                      <p>复核：{f.review_metric || "待记录"}</p>
                      <small>{when(f.at)}</small>
                    </div>
                  </div>
                ))}
                {manager && (
                  <>
                    <label>
                      跟进建议
                      <select
                        value={suggestion}
                        onChange={(e) => setSuggestion(e.target.value)}
                      >
                        <option value="">整份报告</option>
                        {selected.recommendation
                          .split(/\n+/)
                          .filter(Boolean)
                          .map((s: string) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                      </select>
                    </label>
                    <div className="form-grid">
                      <label>
                        到期日期
                        <input
                          type="date"
                          value={due}
                          onChange={(e) => setDue(e.target.value)}
                        />
                      </label>
                      <label>
                        复核指标与结果
                        <input
                          value={reviewMetric}
                          onChange={(e) => setReviewMetric(e.target.value)}
                        />
                      </label>
                    </div>
                    <label>
                      计划 / 已执行行动
                      <textarea
                        aria-label="计划 / 已执行行动"
                        value={actionText}
                        onChange={(e) => setActionText(e.target.value)}
                      />
                    </label>
                    <div className="form-grid">
                      <label>
                        处理状态
                        <select
                          value={feedback}
                          onChange={(e) => setFeedback(e.target.value)}
                        >
                          {["待评估", "已采纳", "暂缓", "未采纳"].map((s) => (
                            <option key={s}>{s}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        执行负责人
                        <input
                          placeholder="填写负责人"
                          value={owner}
                          onChange={(e) => setOwner(e.target.value)}
                        />
                      </label>
                    </div>
                    <label>
                      反馈说明
                      <textarea
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        placeholder="记录实际跟进情况，不自动执行调价或采购"
                      />
                    </label>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        act("feedback", {
                          state: feedback,
                          owner,
                          comment,
                          suggestion,
                          due,
                          action: actionText,
                          review_metric: reviewMetric,
                        })
                      }
                    >
                      保存反馈
                    </button>
                  </>
                )}
              </>
            )}
          </div>
          <div className="modal-actions sticky-actions">
            <a
              className="btn"
              target="_blank"
              rel="noopener"
              href={`/api/reports/${selected.id}/print`}
            >
              打印 / 保存 PDF
            </a>
            <a className="btn" href={`/api/reports/${selected.id}/export`}>
              <Download size={16} />
              {selected.status === "approved" ? "导出正式报告" : "导出草稿"}
            </a>
            <div className="button-row">
              {canEdit && ["draft", "rejected"].includes(selected.status) && (
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api(`/reports/${selected.id}/edit`, {
                        note: selected.note,
                        recommendation: selected.recommendation,
                      });
                      const r = await api(`/reports/${selected.id}/submit`, {});
                      setSelected(r);
                      setSaved(signature(r));
                      reload();
                      notify("已保存正文并提交审核");
                    } catch (e: any) {
                      notify(e.message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  提交审核 <ArrowRight size={15} />
                </button>
              )}
              {selected.status === "pending" && manager && (
                <>
                  <button
                    className="btn"
                    disabled={busy || !comment.trim()}
                    onClick={() => act("reject", { comment })}
                  >
                    退回修改
                  </button>
                  <button
                    className="btn primary"
                    disabled={busy || !comment.trim()}
                    onClick={() => act("approve", { comment })}
                  >
                    <Check size={16} />
                    审核通过
                  </button>
                </>
              )}
              {selected.status === "approved" && canEdit && (
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={() => act("revise")}
                >
                  创建修订版本 <Plus size={15} />
                </button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
