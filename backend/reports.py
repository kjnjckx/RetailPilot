import asyncio
import html
import re
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from . import contracts as c
from . import storage as db
from .auth import object_for, require_data_role, role, user
from .domain import DomainError

router = APIRouter()
LOCK = asyncio.Lock()


@router.post("/api/reports")
async def create_report(request: Request):
    u = user(request)
    role(u, "analyst", "manager")
    p = await c.body(request, c.Report)
    t = object_for(u, "tasks", p.get("task_id", ""))
    if t["status"] != "completed" or t["result"]["current"]["empty"]:
        raise DomainError("仅有完整证据和有效数据的成功任务可以生成报告")
    r = {
        "id": db.uid("RP"),
        "at": db.now(),
        "task_id": t["id"],
        "version_id": t["version_id"],
        "metric_version": t["metric_version"],
        "scope": t["scope"],
        "title": t["question"],
        "revision": 1,
        "status": "draft",
        "summary": t["result"]["summary"],
        "note": "",
        "recommendation": t["result"]["recommendation"],
        "result": t["result"],
        "reviews": [],
        "feedback": [],
        "creator": u["name"],
    }
    report_evidence(r)
    db.put("reports", r)
    db.audit(u, "生成报告草稿", r["id"])
    return r


@router.get("/api/reports")
async def reports(
    request: Request,
    page: int | None = Query(None, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=30),
):
    u = user(request)
    require_data_role(u)
    result = await asyncio.to_thread(
        db.summaries,
        "reports",
        u["stores"],
        False,
        not bool(set(u["roles"]).intersection(["analyst", "manager"])),
        page or 1,
        page_size,
        q,
        status,
    )
    return result if page is not None else result["items"]


@router.get("/api/reports/{id}")
async def report_detail(id: str, request: Request):
    return object_for(user(request), "reports", id)


@router.post("/api/reports/{id}/{action}")
async def report_action(id: str, action: str, request: Request):
    u = user(request)
    model = {
        "edit": c.Edit,
        "approve": c.Review,
        "reject": c.Review,
        "feedback": c.Feedback,
        "submit": c.Empty,
        "revise": c.Empty,
    }.get(action)
    if not model:
        raise DomainError("未知操作", 404)
    p = await c.body(request, model)
    with db.connection() as transaction:
        transaction.execute("BEGIN IMMEDIATE")
        r = object_for(u, "reports", id)
        if action in ["approve", "reject", "feedback"]:
            role(u, "manager")
        else:
            role(u, "analyst", "manager")
        if action == "edit":
            if r["status"] not in ["draft", "rejected"]:
                raise DomainError("当前报告正文不可编辑")
            for key in ["title", "note", "recommendation"]:
                r[key] = str(p.get(key, r[key]))[:6000]
        elif action == "submit":
            if r["status"] not in ["draft", "rejected"]:
                raise DomainError("仅草稿或退回报告可以提交")
            report_evidence(r, require_version=True)
            r["status"] = "pending"
        elif action in ["approve", "reject"]:
            if r["status"] != "pending":
                raise DomainError("报告尚未提交审核")
            comment = str(p.get("comment", "")).strip()[:2000]
            if not comment:
                raise DomainError("请填写审核意见")
            if action == "approve":
                report_evidence(r, require_version=True)
            r["status"] = "approved" if action == "approve" else "rejected"
            r["reviews"].append(
                {
                    "at": db.now(),
                    "actor": u["name"],
                    "decision": r["status"],
                    "comment": comment,
                    "revision": r["revision"],
                }
            )
        elif action == "revise":
            if r["status"] != "approved":
                raise DomainError("仅已通过报告可以创建新版本")
            r.update(
                id=db.uid("RP"),
                parent_id=id,
                status="draft",
                revision=r["revision"] + 1,
                at=db.now(),
                reviews=[],
                feedback=[],
            )
        elif action == "feedback":
            if r["status"] != "approved":
                raise DomainError("请先完成报告审核")
            if p["due"]:
                try:
                    from datetime import date

                    date.fromisoformat(p["due"])
                except ValueError:
                    raise DomainError("建议到期日格式无效")
            if (
                p["suggestion"]
                and p["suggestion"] not in r["recommendation"].splitlines()
            ):
                raise DomainError("建议不属于当前报告")
            if p.get("state") not in ["待评估", "已采纳", "暂缓", "未采纳"]:
                raise DomainError("反馈状态无效")
            r["feedback"].append(
                {
                    "at": db.now(),
                    "actor": u["name"],
                    "state": p["state"],
                    "owner": str(p.get("owner", ""))[:100],
                    "comment": p["comment"],
                    "suggestion": p["suggestion"],
                    "due": p["due"],
                    "action": p["action"],
                    "review_metric": p["review_metric"],
                }
            )
        else:
            raise DomainError("未知操作", 404)
        db.save(transaction, "reports", r)
    db.audit(u, "报告操作：" + action, r["id"])
    return r


def report_evidence(report, require_version=False):
    result = report.get("result", {})
    evidence = result.get("evidence")
    if not evidence or not evidence.get("metric_version"):
        raise DomainError("报告缺少固定指标版本的核心证据")
    pinned = report.get("metric_version") or evidence["metric_version"]
    if (
        pinned != evidence["metric_version"]
        or result.get("metric_version", pinned) != pinned
        or evidence.get("version_id") != report["version_id"]
        or set(evidence.get("scope", [])) != set(report["scope"])
        or result.get("current") != evidence.get("current")
        or result.get("previous") != evidence.get("previous")
        or result.get("metric_results") != evidence.get("metric_results")
    ):
        raise DomainError("报告与证据的数据版本、指标版本、范围或数值不一致")
    if require_version and not db.get("versions", report["version_id"], full=False):
        raise DomainError("原始数据版本不可用，无法审核或导出正式报告")
    return evidence, pinned


def saved_metrics(result):
    if result.get("metric_results"):
        return result["metric_results"]
    # Existing reports retain their saved sales analysis; never reinterpret their question.
    return [
        {
            "metric": "net",
            "label": "净销售额",
            "unit": "元",
            "storage_unit": "人民币分",
            "current": None if result["current"]["empty"] else result["current"]["net"],
            "previous": result["previous"]["net"]
            if result.get("previous") and not result["previous"]["empty"]
            else None,
            "delta": result.get("delta"),
            "rate": result.get("rate"),
            "rankings": [
                {**row, "value": row["net"]} for row in result["current"]["stores"]
            ],
            "category_values": [
                {**row, "value": row["net"]} for row in result["current"]["categories"]
            ],
            "contributions": [
                {**row, "value": row["net"]} for row in result.get("contributions", [])
            ],
            "category_contributions": [
                {**row, "value": row["net"]}
                for row in result.get("category_contributions", [])
            ],
        }
    ]


def saved_value(metric, value):
    if value is None:
        return "不适用"
    if metric["storage_unit"] == "人民币分":
        return f"{Decimal(value) / 100:.2f} 元"
    return f"{value} {metric['unit']}"


@router.get("/api/reports/{id}/export")
async def export(id: str, request: Request):
    u = user(request)
    r = object_for(u, "reports", id)
    e, metric_version = report_evidence(r, require_version=r["status"] == "approved")
    v = db.get("versions", r["version_id"], full=False)
    result = r["result"]
    plan = e["plan"]
    baseline = (
        f"{plan['previous_start']} 至 {plan['previous_end']}"
        if plan["compare"]
        else "无（单期统计）"
    )
    names = {s["store_id"]: s["name"] for s in v.get("store_meta", [])} if v else {}
    scope = "、".join(f"{names.get(id, id)}（{id}）" for id in r["scope"])
    lines = [
        f"# {r['title']}",
        f"状态：{'正式报告 · 已审核' if r['status'] == 'approved' else '草稿 · 未经审核'} | 修订 {r['revision']}",
        f"数据版本：{r['version_id']} | 指标版本：{metric_version}",
        f"数据来源：{v.get('source', '未记录')} | 快照时间：{v.get('snapshot_at', '未记录')}"
        if v
        else "数据来源：原始版本不可用",
        f"证据：{e['id']} | 查询指纹：{e['fingerprint']}",
        f"范围：本期 {plan['start']} 至 {plan['end']}；基期 {baseline}；门店 {scope}",
        "## 数据事实",
        r["summary"],
        "|指标|本期|基期|变化额|变化率|",
        "|---|---:|---:|---:|---:|",
    ]
    metrics = saved_metrics(result)
    for metric in metrics:
        rate = f"{metric['rate']:+.2f}%" if metric["rate"] is not None else "不适用"
        lines.append(
            f"|{metric['label']}|{saved_value(metric, metric['current'])}|{saved_value(metric, metric['previous'])}|{saved_value(metric, metric['delta'])}|{rate}|"
        )
    lines += [
        "## 业务说明",
        r["note"],
        "## 建议（需人工判断）",
        r["recommendation"],
        "## 限制",
        result["limits"],
    ]
    for metric in metrics:
        lines += [
            f"## {metric['label']}：门店排名（{metric['unit']}）",
            "|门店|本期值|",
            "|---|---:|",
        ]
        lines += [
            f"|{row['name']}|{saved_value(metric, row['value'])}|"
            for row in metric["rankings"]
        ]
        lines += [
            f"## {metric['label']}：趋势（{metric['unit']}）",
            "|日期|本期值|",
            "|---|---:|",
        ]
        lines += [
            f"|{row['date']}|{saved_value(metric, row[metric['metric']])}|"
            for row in result["current"]["daily"]
        ]
        lines += [
            f"## {metric['label']}：品类（{metric['unit']}）",
            "|品类|本期值|",
            "|---|---:|",
        ]
        lines += [
            f"|{row['name']}|{saved_value(metric, row['value'])}|"
            for row in metric["category_values"]
        ]
        for label, rows in [
            ("门店变化贡献", metric["contributions"]),
            ("品类变化贡献", metric["category_contributions"]),
        ]:
            if rows:
                lines += [
                    f"## {metric['label']}：{label}（{metric['unit']}）",
                    "|维度|基期|本期|变化额|",
                    "|---|---:|---:|---:|",
                ]
                lines += [
                    f"|{row['name']}|{saved_value(metric, row['previous'])}|{saved_value(metric, row['value'])}|{saved_value(metric, row['delta'])}|"
                    for row in rows
                ]
        if metric.get("formula"):
            lines.append(f"指标口径：{metric['formula']}")
    lines += ["## 审核记录"] + [
        f"- {x['actor']} / {x['at']} / {x['decision']}：{x['comment']}"
        for x in r["reviews"]
    ]
    lines += ["## 建议反馈"] + [
        f"- {x['state']} / 负责人：{x['owner']} / 建议：{x.get('suggestion') or '整份报告'} / 到期：{x.get('due') or '未设置'} / 行动：{x.get('action') or '待记录'} / 复核：{x.get('review_metric') or '待记录'} / {x['comment']}"
        for x in r["feedback"]
    ]
    db.audit(u, "导出报告", id)
    return Response(
        "\n".join(
            "\n" + line + "\n" if line.startswith("#") else line for line in lines
        ),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{id}.md"'},
    )


def print_body(markdown):
    lines = markdown.splitlines()
    parts = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|") and line.endswith("|"):
            rows = []
            while (
                index < len(lines)
                and lines[index].startswith("|")
                and lines[index].endswith("|")
            ):
                cells = lines[index].strip("|").split("|")
                if not all(re.fullmatch(r":?-+:?", c.strip()) for c in cells):
                    rows.append(cells)
                index += 1
            if rows:
                parts.append(
                    "<table><thead><tr>"
                    + "".join("<th>" + html.escape(c) + "</th>" for c in rows[0])
                    + "</tr></thead><tbody>"
                )
                parts.extend(
                    "<tr>"
                    + "".join("<td>" + html.escape(c) + "</td>" for c in row)
                    + "</tr>"
                    for row in rows[1:]
                )
                parts.append("</tbody></table>")
            continue
        if line.startswith("#"):
            count = min(3, len(line) - len(line.lstrip("#")))
            parts.append(
                f"<h{count}>" + html.escape(line.lstrip("# ")) + f"</h{count}>"
            )
        elif line:
            parts.append("<p>" + html.escape(line) + "</p>")
        index += 1
    return "".join(parts)


@router.get("/api/reports/{id}/print")
async def print_report(id: str, request: Request):
    exported = await export(id, request)
    text = print_body(exported.body.decode("utf-8"))
    document = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>RetailPilot 报告</title><style>
body{font:14px/1.7 system-ui,sans-serif;margin:32px auto;max-width:900px;color:#172b3a}h1{font-size:24px}h2{font-size:18px;margin-top:28px;border-bottom:1px solid #c8d5dd}h1,h2,h3{break-after:avoid}p{overflow-wrap:anywhere;white-space:pre-wrap}table{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0}thead{display:table-header-group}th,td{border:1px solid #c8d5dd;padding:6px 9px;text-align:left;overflow-wrap:anywhere}th{background:#f0f5f7}tr{break-inside:avoid}button{padding:12px}@media print{button{display:none}body{margin:0;max-width:none;font-size:10pt}table{font-size:9pt}}@page{size:A4;margin:18mm}
</style><button onclick="window.print()">打印 / 保存 PDF</button>"""
        + text
        + "</html>"
    )
    return Response(
        document,
        media_type="text/html",
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        },
    )
