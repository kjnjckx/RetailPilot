export type Obj = Record<string, any>;
export type MetricKey = "net" | "orders" | "average" | "quantity";
export type MetricRow = {
  name: string;
  store_id?: string;
  value: number | null;
  previous: number | null;
  delta: number | null;
  empty?: boolean;
};
export type MetricResult = {
  metric: MetricKey;
  label: string;
  unit: string;
  storage_unit: string;
  current: number | null;
  previous: number | null;
  delta: number | null;
  rate: number | null;
  rankings: MetricRow[];
  category_values: MetricRow[];
  contributions: MetricRow[];
  category_contributions: MetricRow[];
  formula?: string;
  rule?: string;
};
export const money = (n: number | null | undefined) =>
  n == null
    ? "—"
    : (n / 100).toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
export const num = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("zh-CN");
export const formatMetric = (metric: MetricResult, value: number | null) =>
  value == null
    ? "不适用"
    : metric.storage_unit === "人民币分"
      ? `¥${money(value)}`
      : `${num(value)} ${metric.unit}`;
export function resultMetrics(result: Obj): MetricResult[] {
  if (result.metric_results?.length) return result.metric_results;
  // Historical results describe saved sales figures, regardless of their original question.
  const rows = (values: Obj[]) =>
    values.map((row) => ({ ...row, value: row.net })) as MetricRow[];
  return [
    {
      metric: "net",
      label: "净销售额",
      unit: "元",
      storage_unit: "人民币分",
      current: result.current.empty ? null : result.current.net,
      previous:
        result.previous && !result.previous.empty ? result.previous.net : null,
      delta: result.delta ?? null,
      rate: result.rate ?? null,
      rankings: rows(result.current.stores),
      category_values: rows(result.current.categories),
      contributions: rows(result.contributions || []),
      category_contributions: rows(result.category_contributions || []),
    },
  ];
}
export const when = (v: string) =>
  !v
    ? "—"
    : new Date(v).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
export function latestCompleteMonth(start: string, end: string) {
  if (!start || !end) return "";
  const first = new Date(end.slice(0, 7) + "-01T00:00:00Z");
  const last = new Date(
    Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0),
  );
  if (end < last.toISOString().slice(0, 10))
    first.setUTCMonth(first.getUTCMonth() - 1);
  if (first.toISOString().slice(0, 10) < start) return "";
  return `${first.getUTCFullYear()} 年 ${first.getUTCMonth() + 1} 月`;
}
export const statusLabel: Obj = {
  draft: "草稿",
  pending: "待审核",
  approved: "已通过",
  rejected: "已退回",
  queued: "排队中",
  running: "分析中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  clarification: "待澄清",
  validated: "校验通过",
  invalid: "校验失败",
  published: "已发布",
};
export const tableLabels: Obj = {
  stores: "门店",
  products: "商品",
  orders: "订单",
  items: "订单明细",
};
export const fields: Record<string, string[]> = {
  stores: ["store_id", "name", "region"],
  products: ["product_id", "name", "category"],
  orders: [
    "order_id",
    "store_id",
    "paid_at",
    "status",
    "paid_amount",
    "refund_amount",
  ],
  items: [
    "order_id",
    "line_id",
    "product_id",
    "quantity",
    "returned_quantity",
    "paid_amount",
    "refund_amount",
  ],
};
export async function api(path: string, body?: Obj, signal?: AbortSignal) {
  const res = await fetch("/api" + path, {
    signal,
    method: body ? "POST" : "GET",
    headers: body
      ? {
          "Content-Type": "application/json",
          "X-Requested-With": "RetailPilot",
        }
      : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res
    .json()
    .catch(() => ({ error: "服务暂时不可用，请稍后重试" }));
  if (res.status === 401 && path !== "/login")
    window.dispatchEvent(new Event("retailpilot:unauthorized"));
  if (!res.ok) throw new Error(data.error || "请求失败，请稍后重试");
  return data;
}
export function sparkPath(values: (number | null)[]) {
  const valid = values.filter((v): v is number => v != null);
  if (valid.length < 2) return "";
  const min = Math.min(...valid),
    span = Math.max(...valid) - min || 1;
  return valid
    .map(
      (v, i) =>
        `${i ? "L" : "M"}${(i / (valid.length - 1)) * 80} ${22 - ((v - min) / span) * 20}`,
    )
    .join(" ");
}

export function readLocation(key: string) {
  return new URLSearchParams(location.search).get(key) || "";
}
export function writeLocation(values: Record<string, string>, push = false) {
  const url = new URL(location.href);
  Object.entries(values).forEach(([k, v]) =>
    v ? url.searchParams.set(k, v) : url.searchParams.delete(k),
  );
  history[push ? "pushState" : "replaceState"]({}, "", url);
}
// RFC-style quoted CSV fields, including escaped quotes and embedded newlines.
export function csvHeader(text: string): string[] {
  const result: string[] = [];
  let field = "",
    quoted = false;
  text = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = !quoted;
    } else if (!quoted && (c === "," || c === "\n" || c === "\r")) {
      result.push(field);
      field = "";
      if (c !== ",") return result;
    } else field += c;
  }
  if (quoted) throw new Error("CSV 表头引号未闭合");
  result.push(field);
  return result;
}
export type Role = "admin" | "analyst" | "manager" | "store";
export interface User {
  id: string;
  name: string;
  roles: Role[];
  stores: string[];
  enabled?: boolean;
}
export interface StoreInfo {
  store_id: string;
  name: string;
  region: string;
}
export interface Version {
  id: string;
  name: string;
  at: string;
  snapshot_at: string;
  coverage_start: string;
  coverage_end: string;
  stores: StoreInfo[];
  row_count: number;
  source: string;
  creator: string;
}
export type TaskStatus =
  "queued" | "running" | "completed" | "failed" | "cancelled" | "clarification";
export interface Plan {
  start: string;
  end: string;
  previous_start: string;
  previous_end: string;
  compare: boolean;
  store: string;
  region: string;
  metrics: MetricKey[];
  comparison: string;
  ranking: boolean;
  plan_version: string;
}
export interface Aggregate {
  net: number;
  orders: number;
  quantity: number;
  average: number | null;
  empty: boolean;
  stores: (StoreInfo & Record<MetricKey, number | null>)[];
  categories: Obj[];
  daily: Obj[];
}
export interface Evidence {
  id: string;
  fingerprint: string;
  version_id: string;
  metric_version: string;
  scope: string[];
  plan: Plan;
  current: Aggregate;
  previous: Aggregate | null;
  metric_results: MetricResult[];
}
export interface AnalysisResult {
  current: Aggregate;
  previous: Aggregate | null;
  metric_results: MetricResult[];
  evidence: Evidence;
  summary: string;
  recommendation: string;
  limits: string;
  delta: number | null;
  rate: number | null;
  contributions: MetricRow[];
  category_contributions: MetricRow[];
}
export interface Task {
  id: string;
  at: string;
  user_id: string;
  creator: string;
  question: string;
  version_id: string;
  metric_version: string;
  scope: string[];
  status: TaskStatus;
  engine: string;
  plan?: Plan;
  result?: AnalysisResult;
  error?: string;
  steps: Obj[];
}
export interface Report {
  id: string;
  at: string;
  title: string;
  creator: string;
  version_id: string;
  metric_version: string;
  scope: string[];
  status: "draft" | "pending" | "approved" | "rejected";
  revision: number;
  note: string;
  summary: string;
  recommendation: string;
  result: AnalysisResult;
  reviews: Obj[];
  feedback: Obj[];
}

export type TaskSummary = Omit<Task, "result" | "steps">;
export type ReportSummary = Omit<
  Report,
  "result" | "reviews" | "feedback" | "note" | "recommendation"
>;
