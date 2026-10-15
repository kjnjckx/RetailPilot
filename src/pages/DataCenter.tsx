import React, { useEffect, useState, useRef } from "react";
import {
  ArrowRight,
  CheckCheck,
  ChevronRight,
  Database,
  Download,
  Plus,
  ShieldCheck,
  UploadCloud,
  XCircle,
  Loader2,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import {
  Obj,
  User,
  Version,
  num,
  when,
  tableLabels,
  fields,
  api,
  csvHeader,
} from "../lib";
import { Modal, Badge, Empty } from "../components/ui";

function snapshotAfter(date: string) {
  if (!date) return "";
  const next = new Date(date + "T00:00:00Z");
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10) + "T00:00";
}

export default function DataCenter({
  user,
  versions,
  onRefresh,
  notify,
}: {
  user: User;
  versions: Version[];
  onRefresh: () => void;
  notify: (s: string) => void;
}) {
  const [batches, setBatches] = useState<Obj[]>([]),
    [show, setShow] = useState(false),
    [files, setFiles] = useState<Record<string, string>>({}),
    [mapping, setMapping] = useState<Obj>({}),
    [names, setNames] = useState<Obj>({}),
    [busy, setBusy] = useState(false),
    [batch, setBatch] = useState<Obj | null>(null),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [snapshot, setSnapshot] = useState(""),
    [accepted, setAccepted] = useState(false),
    [name, setName] = useState("零售经营数据快照"),
    [source, setSource] = useState(""),
    [page, setPage] = useState(1),
    [pages, setPages] = useState(1);
  const fileState = useRef<Record<string, string>>({});
  const allowed =
    user.roles.includes("analyst") || user.roles.includes("admin");
  const reload = () => {
    if (allowed)
      api("/imports?page=" + page)
        .then((r) => {
          setBatches(r.items);
          setPages(r.pages);
        })
        .catch((e) => notify(e.message));
  };
  useEffect(reload, [page]);
  async function loadWorkbook(f: File) {
    if (f.size > 20 * 1024 * 1024) {
      notify("Excel 文件不能超过 20 MB");
      return;
    }
    setBusy(true);
    try {
      const bytes = new Uint8Array(await f.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 8192)
        binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      const r = await api("/workbook/convert", { content: btoa(binary) });
      fileState.current = r.files;
      setFiles(r.files);
      setMapping({});
      setNames(
        Object.fromEntries(
          Object.keys(r.files).map((t) => [t, f.name + " · " + t]),
        ),
      );
      notify("工作簿已读取，请检查字段并校验发布");
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function validate() {
    setBusy(true);
    try {
      const result = await api("/imports", { files, mapping, name, source });
      setBatch(result);
      setStart(result.coverage_start);
      setEnd(result.coverage_end);
      setSnapshot(snapshotAfter(result.coverage_end));
      reload();
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function publish() {
    if (!batch) return;
    setBusy(true);
    try {
      await api(`/imports/${batch.id}/publish`, {
        coverage_start: start,
        coverage_end: end,
        snapshot_at: snapshot + ":00+08:00",
        accept_warnings: accepted,
      });
      setShow(false);
      setBatch(null);
      reload();
      onRefresh();
      notify("数据版本已发布，历史报告保持原快照。");
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="data-banner">
        <div className="data-banner-icon">
          <Database size={28} />
        </div>
        <div>
          <h2>可信数据，是经营洞察的起点</h2>
          <p>导入门店、商品、订单与订单明细，完成校验后发布独立的数据快照。</p>
        </div>
        {allowed && (
          <button
            className="btn primary"
            onClick={() => {
              setShow(true);
              setBatch(null);
              fileState.current = {};
              setFiles({});
              setNames({});
              setMapping({});
              setAccepted(false);
              setSource("");
            }}
          >
            <Plus size={17} />
            导入新数据
          </button>
        )}
      </div>
      <div className="section-label">
        <h2>
          已发布数据版本 <span>{versions.length}</span>
        </h2>
        <span>
          <ShieldCheck size={15} />
          不可变快照 · 历史可追溯
        </span>
      </div>
      <div className="version-grid">
        {versions.map((v, i) => (
          <section className="panel version-card" key={v.id}>
            <div className="version-card-top">
              <span className="file-icon">
                <Database size={23} />
              </span>
              <Badge status="published" />
            </div>
            <h3>{v.name}</h3>
            <p>
              {v.id} {i === 0 && <span className="subtle-tag">最新可用</span>}
            </p>
            <dl>
              <div>
                <dt>数据覆盖</dt>
                <dd>
                  {v.coverage_start} — {v.coverage_end}
                </dd>
              </div>
              <div>
                <dt>授权范围内明细</dt>
                <dd>
                  {num(v.row_count)} 行 · {v.stores.length} 家门店
                </dd>
              </div>
              <div>
                <dt>数据来源</dt>
                <dd>{v.source}</dd>
              </div>
              <div>
                <dt>快照时间</dt>
                <dd>{when(v.snapshot_at)}</dd>
              </div>
            </dl>
            <div className="version-foot">
              <span>
                <CheckCircle2 size={14} />
                已通过质量校验
              </span>
              <small>{v.creator}</small>
            </div>
          </section>
        ))}
      </div>
      {!versions.length && (
        <Empty
          text="暂无可访问的数据版本"
          hint="发布校验通过的数据，或申请门店授权。"
        />
      )}
      <section className="panel import-history">
        <div className="panel-heading">
          <div>
            <h2>导入记录</h2>
            <p>每一个批次的校验与发布状态</p>
          </div>
          <UploadCloud size={18} className="muted" />
        </div>
        {!batches.length ? (
          <Empty
            text={allowed ? "还没有导入记录" : "当前角色无导入权限"}
            hint={
              allowed
                ? "点击“导入新数据”，开始准备你的经营数据。"
                : "数据分析人员或系统管理员可以上传和发布经营数据。"
            }
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>批次 / 名称</th>
                  <th>时间</th>
                  <th>明细数</th>
                  <th>质量问题</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <b>{b.name}</b>
                      <small className="block muted">{b.id}</small>
                    </td>
                    <td>{when(b.at)}</td>
                    <td>{num(b.row_count)}</td>
                    <td>
                      {b.error_count ?? 0} 错误 · {b.warning_count ?? 0} 警告
                    </td>
                    <td>
                      <Badge status={b.status} />
                    </td>
                    <td>
                      <button
                        className="text-btn"
                        onClick={async () => {
                          try {
                            setBatch(await api("/imports/" + b.id));
                          } catch (e: any) {
                            notify(e.message);
                            return;
                          }
                          setStart(b.coverage_start || "");
                          setEnd(b.coverage_end || "");
                          setSnapshot(snapshotAfter(b.coverage_end));
                          setSource(b.source || "");
                          setShow(true);
                        }}
                      >
                        查看 <ChevronRight size={14} />
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
      {show && (
        <Modal
          wide
          title={batch ? "数据校验结果" : "导入经营数据"}
          onClose={() => setShow(false)}
        >
          {!batch ? (
            <>
              <div className="import-progress">
                <b>1 上传与映射</b>
                <ChevronRight size={14} />
                <span>2 质量校验</span>
                <ChevronRight size={14} />
                <span>3 发布快照</span>
              </div>
              <label>
                数据集名称
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                数据来源（业务系统、导出日期或文件出处）
                <input
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="例如：门店 POS 系统，填写实际导出日期"
                />
              </label>
              <div className="upload-grid">
                <label className="btn">
                  导入 Excel 四表工作簿
                  <input
                    type="file"
                    accept=".xlsx"
                    disabled={busy}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) loadWorkbook(f);
                    }}
                  />
                </label>
                <p className="notice">
                  工作表名：stores、products、orders、items。仅导入值；成交时间填写带
                  +08:00 的文本。
                </p>
                {Object.keys(fields).map((t) => (
                  <div className="upload-box" key={t}>
                    <div>
                      <b>{tableLabels[t]}</b>
                      <a href={"/api/templates/" + t} className="text-btn">
                        <Download size={13} />
                        模板
                      </a>
                    </div>
                    <label className="file-drop">
                      <UploadCloud size={23} />
                      <span>{names[t] || `选择 ${t}.csv`}</span>
                      <small>UTF-8 CSV</small>
                      <input
                        aria-label={`上传${tableLabels[t]}CSV`}
                        type="file"
                        accept=".csv"
                        onChange={async (e) => {
                          const f = e.target.files?.[0];
                          if (!f) return;
                          try {
                            const text = new TextDecoder("utf-8", {
                              fatal: true,
                            }).decode(await f.arrayBuffer());
                            const next = { ...fileState.current, [t]: text };
                            if (
                              Object.values(next).reduce(
                                (n, s) =>
                                  n + new TextEncoder().encode(s).length,
                                0,
                              ) >
                              20 * 1024 * 1024
                            )
                              throw new Error("四表合计不能超过 20 MB");
                            csvHeader(text);
                            fileState.current = next;
                            setFiles(next);
                          } catch (err) {
                            notify(
                              err instanceof Error
                                ? "读取失败：" +
                                    err.message +
                                    "。请使用 UTF-8 CSV。"
                                : "文件读取失败",
                            );
                            return;
                          }
                          setNames((old) => ({ ...old, [t]: f.name }));
                          setMapping((old) => ({ ...old, [t]: {} }));
                        }}
                      />
                    </label>
                    {files[t] && (
                      <details>
                        <summary>字段映射与预览</summary>
                        {fields[t].map((f) => (
                          <label className="mapping-row" key={f}>
                            <span>{f}</span>
                            <select
                              value={mapping[t]?.[f] || f}
                              onChange={(e) =>
                                setMapping((m) => ({
                                  ...m,
                                  [t]: { ...m[t], [f]: e.target.value },
                                }))
                              }
                            >
                              <option value={f}>{f}</option>
                              {csvHeader(files[t])
                                .filter((h) => h !== f)
                                .map((h) => (
                                  <option key={h}>{h}</option>
                                ))}
                            </select>
                          </label>
                        ))}
                        <pre>
                          {files[t].split(/\r?\n/).slice(0, 4).join("\n")}
                        </pre>
                      </details>
                    )}
                  </div>
                ))}
              </div>
              <div className="notice">
                字段金额以元填写，成交时间需包含 +08:00
                时区。快照需包含全部四张表；阻断错误不会被自动跳过。数据来源由上传人填写，请与原始系统记录核对。
              </div>
              <div className="modal-actions">
                <button
                  className="btn primary"
                  disabled={
                    busy ||
                    !source.trim() ||
                    Object.keys(fields).some((t) => !files[t])
                  }
                  onClick={validate}
                >
                  {busy ? (
                    <Loader2 size={16} className="spin" />
                  ) : (
                    <CheckCheck size={16} />
                  )}
                  执行质量校验
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="validation-summary">
                <div>
                  <Badge status={batch.status} />
                  <h3>{batch.name}</h3>
                  <p>
                    {batch.row_count} 行明细 · {batch.issues.length} 项质量提示
                  </p>
                </div>
                <a className="btn" href={`/api/imports/${batch.id}/errors`}>
                  <Download size={15} />
                  下载问题清单
                </a>
              </div>
              {batch.duplicate && (
                <div className="notice">
                  业务内容与 {batch.duplicate}{" "}
                  一致。仅在覆盖声明或快照时间确有变化时发布新版本；完全相同的快照会被拒绝。
                </div>
              )}
              {batch.issues.length > 0 && (
                <div className="issue-list">
                  {batch.issues.slice(0, 100).map((i: Obj, n: number) => (
                    <div key={n}>
                      <span
                        className={i.level === "error" ? "negative" : "warning"}
                      >
                        {i.level === "error" ? (
                          <XCircle size={16} />
                        ) : (
                          <AlertCircle size={16} />
                        )}
                      </span>
                      <b>
                        {tableLabels[i.table]} · 第 {i.row} 行
                      </b>
                      <span>{i.message}</span>
                    </div>
                  ))}
                  {batch.issues.length > 100 && (
                    <p>仅展示前 100 条，全部问题请下载清单。</p>
                  )}
                </div>
              )}
              {batch.status === "validated" && (
                <>
                  <div className="form-grid">
                    <label>
                      覆盖开始
                      <input
                        type="date"
                        value={start}
                        onChange={(e) => setStart(e.target.value)}
                      />
                    </label>
                    <label>
                      覆盖结束
                      <input
                        type="date"
                        value={end}
                        onChange={(e) => setEnd(e.target.value)}
                      />
                    </label>
                    <label>
                      快照时间（北京时间）
                      <input
                        type="datetime-local"
                        value={snapshot}
                        onChange={(e) => setSnapshot(e.target.value)}
                      />
                    </label>
                  </div>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={accepted}
                      onChange={(e) => setAccepted(e.target.checked)}
                    />
                    我确认上述日期内的数据完整，并已复核质量警告。
                  </label>
                </>
              )}
              <div className="modal-actions">
                <button className="btn" onClick={() => setBatch(null)}>
                  重新上传
                </button>
                {batch.status === "validated" && (
                  <button
                    className="btn primary"
                    disabled={busy || !accepted}
                    onClick={publish}
                  >
                    发布数据版本 <ArrowRight size={15} />
                  </button>
                )}
              </div>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
