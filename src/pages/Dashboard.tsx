import React, { useEffect, useState } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  CalendarDays,
  FileText,
  ShieldCheck,
  Sparkles,
  Store,
  Users,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Obj,
  Version,
  money,
  num,
  api,
  sparkPath,
  latestCompleteMonth,
  readLocation,
  writeLocation,
} from "../lib";
import { Modal, Empty } from "../components/ui";

export default function Dashboard({
  version,
  versions,
  setVersion,
  ask,
  notify,
}: {
  version: string;
  versions: Version[];
  setVersion: (s: string) => void;
  ask: (q: string) => void;
  notify: (s: string) => void;
}) {
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [store, setStore] = useState(""),
    [data, setData] = useState<Obj | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [metricModal, setMetricModal] = useState(false),
    [showTable, setShowTable] = useState(false);
  useEffect(() => {
    const v = versions.find((x) => x.id === version);
    if (v) {
      const restore =
        readLocation("version") === version && readLocation("start");
      if (restore) {
        setStart(readLocation("start"));
        setEnd(readLocation("end"));
        setStore(readLocation("store"));
        return;
      }
      setStart(
        [v.coverage_start, v.coverage_end.slice(0, 7) + "-01"].sort().at(-1)!,
      );
      setEnd(v.coverage_end);
      setStore("");
    }
  }, [version]);
  useEffect(() => {
    if (!version || !start || !end) return;
    let alive = true;
    setBusy(true);
    setError("");
    api("/dashboard?" + new URLSearchParams({ version, start, end, store }))
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => {
        if (alive) {
          setError(e.message);
          setData(null);
        }
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [version, start, end, store]);
  useEffect(() => {
    if (start && end && data) writeLocation({ start, end, store });
  }, [start, end, store, data]);
  const cur = data?.current,
    prev = data?.previous;
  const rate = (key: string) =>
    prev && prev[key] && !cur?.empty && !prev.empty
      ? ((cur[key] - prev[key]) / prev[key]) * 100
      : null;
  const chart = (cur?.daily || []).map((d: Obj, i: number) => ({
    ...d,
    label: d.date.slice(5).replace("-", "/"),
    value: d.net / 100,
    previous: prev?.daily[i]?.net / 100,
  }));
  const stores = versions.find((v) => v.id === version)?.stores || [];
  const selected = versions.find((v) => v.id === version);
  const month =
    start.endsWith("-01") &&
    latestCompleteMonth(start, end) ===
      `${Number(start.slice(0, 4))} 年 ${Number(start.slice(5, 7))} 月` &&
    start.slice(0, 7) === end.slice(0, 7)
      ? latestCompleteMonth(start, end)
      : "";
  const askFiltered = (q: string) => {
    if (!month) {
      notify("当前筛选不是完整自然月。请选定一个完整月份后进入分析。");
      return;
    }
    ask(q);
  };
  const cards = [
    ["净销售额", "net", "¥", BarChart3],
    ["有效订单数", "orders", "", FileText],
    ["客单价", "average", "¥", Users],
    ["净销量", "quantity", "", Store],
  ] as const;
  return (
    <>
      <div className="filterbar">
        <div className="filter-group">
          <CalendarDays size={16} />
          <input
            aria-label="开始日期"
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
          <span>—</span>
          <input
            aria-label="结束日期"
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </div>
        <div className="filter-group">
          <Store size={16} />
          <select
            aria-label="门店筛选"
            value={store}
            onChange={(e) => setStore(e.target.value)}
          >
            <option value="">全部授权门店</option>
            {stores.map((s: Obj) => (
              <option key={s.store_id} value={s.store_id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="version-filter">
          <span className="live-dot" />
          <select
            aria-label="数据版本"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.id} · 已发布
              </option>
            ))}
          </select>
        </div>
        <button className="text-btn" onClick={() => setMetricModal(true)}>
          <BookOpen size={14} />
          指标口径
        </button>
      </div>
      {error && <div className="notice danger">{error}</div>}
      {!version && (
        <Empty
          text="还没有可访问的数据"
          hint="请导入数据，或联系管理员配置门店权限。"
        />
      )}
      {version && (
        <>
          <div className={"kpi-grid " + (busy ? "loading-fade" : "")}>
            {cards.map(([label, key, prefix, Icon], idx) => {
              const r = rate(key);
              return (
                <div className="kpi-card" key={key}>
                  <div className="kpi-label">
                    {label}
                    <span className={"kpi-icon icon-" + idx}>
                      <Icon size={17} />
                    </span>
                  </div>
                  <div className="kpi-value">
                    <small>{prefix}</small>
                    {cur?.empty
                      ? "—"
                      : prefix
                        ? money(cur?.[key])
                        : num(cur?.[key])}
                    <span>
                      {key === "orders" ? "笔" : key === "quantity" ? "件" : ""}
                    </span>
                  </div>
                  <div className="kpi-bottom">
                    <span
                      className={
                        r == null ? "muted" : r < 0 ? "negative" : "positive"
                      }
                    >
                      {r != null &&
                        (r < 0 ? (
                          <ArrowDownRight size={14} />
                        ) : (
                          <ArrowUpRight size={14} />
                        ))}
                      {r == null
                        ? "无可比数据"
                        : `${r > 0 ? "+" : ""}${r.toFixed(1)}%`}
                    </span>
                    <span>较上一等长周期</span>
                    <svg
                      viewBox="0 0 80 24"
                      className="sparkline"
                      aria-hidden="true"
                    >
                      <path
                        d={sparkPath(
                          (cur?.daily || []).map((d: Obj) => d[key]),
                        )}
                        fill="none"
                        stroke={r != null && r < 0 ? "#b9a69c" : "#7b9f83"}
                        strokeWidth="2"
                      />
                    </svg>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="chart-layout">
            <section className="panel trend-panel">
              <div className="panel-heading">
                <div>
                  <h2>
                    销售趋势 <span className="subtle-tag">日粒度</span>
                  </h2>
                  <p>
                    {start} 至 {end} · 净销售额
                  </p>
                </div>
                <div className="segmented">
                  <button
                    className={!showTable ? "selected" : ""}
                    onClick={() => setShowTable(false)}
                  >
                    趋势图
                  </button>
                  <button
                    className={showTable ? "selected" : ""}
                    onClick={() => setShowTable(true)}
                  >
                    数据表
                  </button>
                </div>
              </div>
              {cur?.empty || !data ? (
                <Empty
                  text={busy ? "正在读取经营数据…" : "所选范围暂无成交记录"}
                  hint="调整日期或门店后再试。"
                />
              ) : showTable ? (
                <div className="chart-table">
                  <table>
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>净销售额（元）</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cur.daily.map((d: Obj) => (
                        <tr key={d.date}>
                          <td>{d.date}</td>
                          <td>¥{money(d.net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <>
                  <div className="chart-unit">单位：元</div>
                  <div className="trend-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart
                        data={chart}
                        margin={{ left: 0, right: 16, top: 10, bottom: 0 }}
                      >
                        <defs>
                          <linearGradient
                            id="salesFill"
                            x1="0"
                            y1="0"
                            x2="0"
                            y2="1"
                          >
                            <stop
                              offset="0%"
                              stopColor="#668d65"
                              stopOpacity={0.24}
                            />
                            <stop
                              offset="100%"
                              stopColor="#668d65"
                              stopOpacity={0}
                            />
                          </linearGradient>
                        </defs>
                        <CartesianGrid
                          strokeDasharray="3 5"
                          vertical={false}
                          stroke="#e9ece7"
                        />
                        <XAxis
                          dataKey="label"
                          axisLine={false}
                          tickLine={false}
                          minTickGap={35}
                          tick={{ fill: "#92988e", fontSize: 11 }}
                          dy={8}
                        />
                        <YAxis
                          axisLine={false}
                          tickLine={false}
                          tick={{ fill: "#92988e", fontSize: 11 }}
                          width={48}
                          tickFormatter={(n) =>
                            n >= 1000 ? `${(n / 1000).toFixed(0)}k` : n
                          }
                        />
                        <Tooltip
                          formatter={(n: any, name: any) => [
                            "¥" +
                              Number(n).toLocaleString("zh-CN", {
                                maximumFractionDigits: 2,
                              }),
                            name === "value" ? "本期" : "上一周期",
                          ]}
                          contentStyle={{
                            border: "1px solid #e2e7dd",
                            borderRadius: 10,
                            fontSize: 12,
                          }}
                        />
                        <Area
                          isAnimationActive={false}
                          type="monotone"
                          dataKey="previous"
                          stroke="#b9c4ac"
                          fill="none"
                          strokeDasharray="5 5"
                          strokeWidth={1.5}
                        />
                        <Area
                          isAnimationActive={false}
                          type="monotone"
                          dataKey="value"
                          stroke="#357052"
                          strokeWidth={2.5}
                          fill="url(#salesFill)"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="chart-legend">
                    <span>
                      <i />
                      本期
                    </span>
                    <span>
                      <i className="dashed" />
                      上一等长周期
                    </span>
                    <small>
                      {data?.previous
                        ? `基期 ${data.previous_start} — ${data.previous_end}`
                        : "基期未完整覆盖，不作比较"}
                    </small>
                  </div>
                </>
              )}
            </section>
            <section className="insight-panel">
              <div className="insight-title">
                <span>
                  <Sparkles size={19} />
                  经营观察
                </span>
                <span className="insight-pill">规则洞察</span>
              </div>
              <div className="insight-art">
                <div className="orbit one" />
                <div className="orbit two" />
                <Sparkles size={28} />
                <span className="orbit-dot" />
              </div>
              <span className="eyebrow">LOOK BEYOND THE NUMBERS</span>
              <h2>
                {rate("net") != null && rate("net")! < 0
                  ? "销售变化，值得进一步关注"
                  : "找到数字背后的下一步"}
              </h2>
              <p>
                {rate("net") == null
                  ? "选定完整比较周期，从门店和品类两条线索理解经营表现。"
                  : `当前范围净销售额较上一周期${rate("net")! < 0 ? "下降" : "增长"} ${Math.abs(rate("net")!).toFixed(1)}%。通过维度拆解，了解变化集中在哪里。`}
              </p>
              <div className="insight-note">
                <ShieldCheck size={15} />
                事实有证据，解释待验证
              </div>
              <button
                onClick={() =>
                  askFiltered(
                    month
                      ? `${month}${stores.find((s: Obj) => s.store_id === store)?.name || ""}净销售额是多少？`
                      : "",
                  )
                }
              >
                深入分析经营变化 <ArrowRight size={17} />
              </button>
            </section>
          </div>
          <div className="lower-grid">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>门店表现</h2>
                  <p>按净销售额排序 · {cur?.stores.length || 0} 家门店</p>
                </div>
                <span className="panel-unit">单位：元</span>
              </div>
              <div className="store-table">
                <table>
                  <thead>
                    <tr>
                      <th>排名 / 门店</th>
                      <th>净销售额</th>
                      <th>销售占比</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {cur?.stores.map((s: Obj, i: number) => (
                      <tr key={s.store_id}>
                        <td>
                          <span className={"rank rank-" + i}>
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <span className="store-cell">
                            {s.name}
                            <small>
                              {s.region} · {s.store_id}
                            </small>
                          </span>
                        </td>
                        <td className="numeric">¥{money(s.net)}</td>
                        <td>
                          <div className="share-cell">
                            <div className="mini-track">
                              <i
                                style={{
                                  width: `${cur.net ? (s.net / cur.net) * 100 : 0}%`,
                                }}
                              />
                            </div>
                            <span>
                              {cur.net
                                ? ((s.net / cur.net) * 100).toFixed(1)
                                : "0"}
                              %
                            </span>
                          </div>
                        </td>
                        <td>
                          <button
                            title={`分析${s.name}`}
                            className="icon-btn"
                            onClick={() =>
                              askFiltered(
                                month
                                  ? `${month}${s.name}净销售额是多少？`
                                  : "",
                              )
                            }
                          >
                            <ArrowUpRight size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel category-panel">
              <div className="panel-heading">
                <div>
                  <h2>品类构成</h2>
                  <p>净销售额占比</p>
                </div>
                <BarChart3 size={18} className="muted" />
              </div>
              <div className="category-total">
                ¥{money(cur?.net)}
                <span>全部品类净销售额</span>
              </div>
              <div className="category-stack">
                {cur?.categories.map((c: Obj, i: number) => (
                  <i
                    title={`${c.name} ¥${money(c.net)}`}
                    key={c.name}
                    style={{
                      width: `${cur.net ? (c.net / cur.net) * 100 : 0}%`,
                      background: ["#2b654c", "#7d9b65", "#b3c892", "#e2e9cf"][
                        i % 4
                      ],
                    }}
                  />
                ))}
              </div>
              <div className="category-list">
                {cur?.categories.map((c: Obj, i: number) => (
                  <div key={c.name}>
                    <span>
                      <i
                        style={{
                          background: [
                            "#2b654c",
                            "#7d9b65",
                            "#b3c892",
                            "#e2e9cf",
                          ][i % 4],
                        }}
                      />
                      {c.name}
                    </span>
                    <b>¥{money(c.net)}</b>
                    <small>
                      {cur.net ? ((c.net / cur.net) * 100).toFixed(1) : 0}%
                    </small>
                  </div>
                ))}
              </div>
            </section>
          </div>
          <div className="quick-ask">
            <span className="quick-icon">
              <Sparkles size={22} />
            </span>
            <div>
              <h3>一个好问题，是更好决策的开始。</h3>
              <p>
                {month
                  ? `试试问：${month}净销售额是多少？`
                  : "发布覆盖完整自然月的数据后，可按月提出经营问题。"}
              </p>
            </div>
            <button
              className="btn"
              onClick={() =>
                askFiltered(month ? `${month}净销售额是多少？` : "")
              }
            >
              去问问零售智航 <ArrowRight size={16} />
            </button>
          </div>
        </>
      )}
      {metricModal && (
        <Modal
          title="统一指标口径 · sales-v1"
          onClose={() => setMetricModal(false)}
        >
          <div className="definition-list">
            {[
              ["净销售额", "有效成交明细实付金额 − 退款金额；排除已取消订单。"],
              [
                "有效订单数",
                "已支付、部分退款、全额退款订单去重数，全额退款仍计数。",
              ],
              ["客单价", "净销售额 ÷ 有效订单数；无订单时显示无数据。"],
              ["净销量", "有效成交明细数量 − 实际退货数量，不按退款金额推算。"],
              [
                "比较周期",
                "总览使用紧邻的上一等长周期，分析工作台使用明确的自然月。",
              ],
              [
                "数据归属",
                "人民币、Asia/Shanghai，以订单成交日归属，按发布快照记录退款。",
              ],
            ].map(([t, d]) => (
              <div key={t}>
                <h3>{t}</h3>
                <p>{d}</p>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
