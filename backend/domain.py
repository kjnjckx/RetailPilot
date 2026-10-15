"""Deterministic retail calculations. All persisted money is integer RMB cents."""

import calendar
import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

METRIC_VERSION = "sales-v1"
METRICS = {
    "net": {
        "label": "净销售额",
        "unit": "元",
        "storage_unit": "人民币分",
        "additive": True,
        "formula": "实付金额合计 − 退款金额合计",
    },
    "orders": {
        "label": "有效订单数",
        "unit": "笔",
        "storage_unit": "笔",
        "additive": True,
        "formula": "非取消订单去重计数（含全额退款订单）",
    },
    "average": {
        "label": "客单价",
        "unit": "元",
        "storage_unit": "人民币分",
        "additive": False,
        "formula": "净销售额 ÷ 有效订单数，按分舍入",
    },
    "quantity": {
        "label": "净销量",
        "unit": "件",
        "storage_unit": "件",
        "additive": True,
        "formula": "成交数量合计 − 退货数量合计",
    },
}
FIELDS = {
    "stores": ["store_id", "name", "region"],
    "products": ["product_id", "name", "category"],
    "orders": [
        "order_id",
        "store_id",
        "paid_at",
        "status",
        "paid_amount",
        "refund_amount",
    ],
    "items": [
        "order_id",
        "line_id",
        "product_id",
        "quantity",
        "returned_quantity",
        "paid_amount",
        "refund_amount",
    ],
}
STATUSES = {"已支付", "部分退款", "全额退款", "已取消"}


class DomainError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def cents(value):
    try:
        n = Decimal(str(value))
        if (
            not n.is_finite()
            or n < 0
            or n > Decimal("999999999999")
            or n.as_tuple().exponent < -2
        ):
            raise ValueError()
        return int(n * 100)
    except (ValueError, InvalidOperation):
        raise ValueError("金额必须为非负数，且最多两位小数")


def integer(value):
    if not re.fullmatch(r"\d+", str(value)):
        raise ValueError("数量必须为非负整数")
    number = int(value)
    if number > 1000000000:
        raise ValueError("单行数量不能超过 1,000,000,000")
    return number


def safe_csv_rows(reader, table, issue):
    try:
        yield from reader
    except csv.Error as exc:
        issue(
            table,
            reader.line_num or 1,
            "CSV 解析失败，请检查引号、转义及超长字段：" + str(exc),
        )


def business_checksum(data):
    digest = hashlib.sha256()
    for table in sorted(data):
        digest.update(table.encode())
        fields = FIELDS[table]
        keys = ("order_id", "line_id") if table == "items" else (fields[0],)
        for row in sorted(data[table], key=lambda row: tuple(row[key] for key in keys)):
            digest.update(
                json.dumps(
                    {k: v for k, v in row.items() if k != "_row"},
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            digest.update(b"\n")
    return digest.hexdigest()


def validate_import(files, mapping=None):
    mapping = mapping or {}
    data, issues = {}, []

    def issue(table, row, message, level="error"):
        issues.append({"table": table, "row": row, "message": message, "level": level})

    for table, fields in FIELDS.items():
        raw = files.get(table, "")
        if not isinstance(raw, str) or not raw.strip():
            issue(table, 1, "缺少 CSV 文件")
            data[table] = []
            continue
        if "\ufffd" in raw or "\x00" in raw:
            issue(table, 1, "编码无效或含空字符，请重新导出 UTF-8 CSV")
            data[table] = []
            continue
        reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")), strict=True)
        try:
            headers = reader.fieldnames or []
        except csv.Error as exc:
            issue(table, reader.line_num or 1, "CSV 表头解析失败：" + str(exc))
            data[table] = []
            continue
        chosen = mapping.get(table, {})
        if len(set(headers)) != len(headers):
            issue(table, 1, "CSV 包含重复列名")
        for field in fields:
            if chosen.get(field, field) not in headers:
                issue(table, 1, f"缺少字段：{field}")
        rows, keys = [], set()
        for record_num, source in enumerate(safe_csv_rows(reader, table, issue), 1):
            rownum = reader.line_num
            if record_num > 100000:
                issue(table, rownum, "单表最多 100,000 行")
                break
            row = {f: (source.get(chosen.get(f, f)) or "").strip() for f in fields}
            row["_row"] = rownum
            try:
                if None in source or any(v is None for v in source.values()):
                    raise ValueError("列数与表头不一致，请检查分隔符或引号")
                if any(len(row[f]) > 65536 for f in fields):
                    raise ValueError("单字段最多 65,536 个字符")
                if any(not row[f] for f in fields):
                    raise ValueError("必填字段为空")
                key = tuple(
                    row[f]
                    for f in (
                        ["order_id", "line_id"] if table == "items" else [fields[0]]
                    )
                )
                if key in keys:
                    raise ValueError("主键重复：" + "/".join(key))
                keys.add(key)
                if table in ("orders", "items"):
                    row["paid_amount"] = cents(row["paid_amount"])
                    row["refund_amount"] = cents(row["refund_amount"])
                    if row["refund_amount"] > row["paid_amount"]:
                        raise ValueError("退款金额超过实付金额")
                if table == "orders":
                    stamp = datetime.fromisoformat(row["paid_at"])
                    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(hours=8):
                        raise ValueError("成交时间必须包含 +08:00 时区")
                    row["paid_at"] = stamp.isoformat()
                    row["date"] = stamp.date().isoformat()
                    if row["status"] not in STATUSES:
                        raise ValueError(
                            "订单状态须为已支付、部分退款、全额退款或已取消"
                        )
                    paid, refund, status = (
                        row["paid_amount"],
                        row["refund_amount"],
                        row["status"],
                    )
                    if (
                        (status == "已支付" and refund != 0)
                        or (status == "部分退款" and not 0 < refund < paid)
                        or (status == "全额退款" and refund != paid)
                    ):
                        raise ValueError("订单状态与退款金额不一致")
                if table == "items":
                    row["quantity"] = integer(row["quantity"])
                    row["returned_quantity"] = integer(row["returned_quantity"])
                    if (
                        row["quantity"] == 0
                        or row["returned_quantity"] > row["quantity"]
                    ):
                        raise ValueError("成交数量须大于零，退货数量不能超过成交数量")
                rows.append(row)
            except (ValueError, TypeError) as exc:
                issue(table, rownum, str(exc))
        data[table] = rows
    stores = {x["store_id"] for x in data["stores"]}
    products = {x["product_id"] for x in data["products"]}
    orders = {x["order_id"]: x for x in data["orders"]}
    totals = defaultdict(lambda: [0, 0, 0])
    for row in data["orders"]:
        if row["store_id"] not in stores:
            issue("orders", row["_row"], "门店外键不存在")
    for row in data["items"]:
        if row["order_id"] not in orders:
            issue("items", row["_row"], "订单外键不存在")
        if row["product_id"] not in products:
            issue("items", row["_row"], "商品外键不存在")
        values = totals[row["order_id"]]
        values[0] += row["paid_amount"]
        values[1] += row["refund_amount"]
        values[2] += 1
        if row["paid_amount"] == row["refund_amount"]:
            issue("items", row["_row"], "明细净额为零，请确认退款快照", "warning")
    for oid, order in orders.items():
        total = totals[oid]
        if total[:2] != [order["paid_amount"], order["refund_amount"]] or not total[2]:
            issue("orders", order["_row"], "订单与明细金额合计不一致，或没有明细")
    if not data["orders"]:
        issue("orders", 1, "至少需要一笔订单")
    if any(
        sum(row[field] for row in data["items"]) > 9007199254740991
        for field in ("paid_amount", "refund_amount", "quantity", "returned_quantity")
    ):
        issue("items", 1, "批次合计超过可精确展示范围，请拆分业务数据范围")
    checksum = business_checksum(data)
    return data, issues, checksum


def scope_stores(data, allowed, store="", region=""):
    available = [s for s in data["stores"] if s["store_id"] in allowed]
    if store and store not in allowed:
        raise DomainError("没有请求门店的数据访问权限", 403)
    if store and not any(s["store_id"] == store for s in data["stores"]):
        raise DomainError("所选版本中没有该门店")
    return [
        s
        for s in available
        if (not store or s["store_id"] == store)
        and (not region or s["region"] == region)
    ]


def aggregate(data, allowed, start, end, store="", region=""):
    try:
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError()
    except ValueError:
        raise DomainError("请选择有效的日期范围")
    stores = scope_stores(data, allowed, store, region)
    ids = {s["store_id"] for s in stores}
    orders = {
        o["order_id"]: o
        for o in data["orders"]
        if o["store_id"] in ids
        and start <= o["date"] <= end
        and o["status"] != "已取消"
    }
    products = {p["product_id"]: p for p in data["products"]}
    daily, bystore, category = defaultdict(int), defaultdict(int), defaultdict(int)
    daily_orders, daily_quantity = defaultdict(int), defaultdict(int)
    store_orders, store_quantity = defaultdict(int), defaultdict(int)
    category_orders, category_quantity = defaultdict(set), defaultdict(int)
    for order in orders.values():
        daily_orders[order["date"]] += 1
        store_orders[order["store_id"]] += 1
    net, quantity = 0, 0
    for item in data["items"]:
        order = orders.get(item["order_id"])
        if not order:
            continue
        amount = item["paid_amount"] - item["refund_amount"]
        net += amount
        quantity += item["quantity"] - item["returned_quantity"]
        daily[order["date"]] += amount
        daily_quantity[order["date"]] += item["quantity"] - item["returned_quantity"]
        bystore[order["store_id"]] += amount
        store_quantity[order["store_id"]] += (
            item["quantity"] - item["returned_quantity"]
        )
        name = products[item["product_id"]]["category"]
        category[name] += amount
        category_quantity[name] += item["quantity"] - item["returned_quantity"]
        category_orders[name].add(order["order_id"])
    duration = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if duration > 3660:
        raise DomainError("单次统计最多支持十年，请缩小日期范围")
    days = [
        (date.fromisoformat(start) + timedelta(days=i)).isoformat()
        for i in range(duration)
    ]
    return {
        "net": net,
        "orders": len(orders),
        "quantity": quantity,
        "average": int((Decimal(net) / len(orders)).quantize(Decimal("1")))
        if orders
        else None,
        "empty": not bool(orders),
        "daily": [
            {
                "date": k,
                "net": daily[k],
                "orders": daily_orders[k],
                "quantity": daily_quantity[k],
                "average": int(
                    (Decimal(daily[k]) / daily_orders[k]).quantize(Decimal("1"))
                )
                if daily_orders[k]
                else None,
            }
            for k in days
        ],
        "stores": sorted(
            [
                dict(
                    s,
                    **dimension_metrics(
                        bystore[s["store_id"]],
                        store_orders[s["store_id"]],
                        store_quantity[s["store_id"]],
                    ),
                )
                for s in stores
            ],
            key=lambda s: -s["net"],
        ),
        "categories": [
            {
                "name": k,
                **dimension_metrics(v, len(category_orders[k]), category_quantity[k]),
            }
            for k, v in sorted(category.items(), key=lambda p: -p[1])
        ],
    }


def dimension_metrics(net, orders, quantity):
    return {
        "net": net,
        "orders": orders,
        "quantity": quantity,
        "average": int((Decimal(net) / orders).quantize(Decimal("1")))
        if orders
        else None,
        "empty": orders == 0,
    }


def metric_text(metric, value):
    if value is None:
        return "不适用"
    if METRICS[metric]["storage_unit"] == "人民币分":
        return f"{Decimal(value) / 100:,.2f} 元"
    return f"{value:,} {METRICS[metric]['unit']}"


def month_range(year, month):
    return (
        f"{year}-{month:02}-01",
        f"{year}-{month:02}-{calendar.monthrange(year, month)[1]}",
    )


def plan_question(question, data, allowed, coverage_start, coverage_end):
    for s in data["stores"]:
        if (s["name"] in question or s["store_id"] in question) and s[
            "store_id"
        ] not in allowed:
            raise DomainError("问题包含未授权门店，无法执行分析", 403)
    if any(
        x in question
        for x in ["库存", "毛利", "利润", "预测", "促销效果", "客户", "客群"]
    ):
        raise DomainError(
            "当前仅有销售快照，不具备所问指标所需的数据。请改问净销售额、订单数、客单价或净销量。",
            422,
        )
    aliases = {
        "net": r"销售额|销售金额|营收|销售(?!量|订单)",
        "orders": r"订单(?!金额)",
        "average": r"客单价",
        "quantity": r"销量|销售量",
    }
    matches = [(key, re.search(pattern, question)) for key, pattern in aliases.items()]
    metrics = [
        key
        for key, match in sorted(
            [(key, match) for key, match in matches if match],
            key=lambda pair: pair[1].start(),
        )
    ]
    if not metrics:
        raise DomainError("请明确分析指标：净销售额、有效订单数、客单价或净销量。", 422)
    years = list(re.finditer(r"(20\d{2})\s*年", question))
    months = list(re.finditer(r"(?<!\d)(1[0-2]|[1-9])\s*月", question))
    if not years or not months or len(years) > 2 or len(months) > 2:
        raise DomainError(
            "请明确完整月份和指标，例如“2026 年 8 月净销售额是多少？”", 422
        )
    if re.search(r"\d+\s*[日号]", question) or re.search(r"月\s*[至到]", question):
        raise DomainError(
            "初版按完整自然月分析；请明确本期月份，再注明要比较的基期月份。", 422
        )
    if any(
        word in question
        for word in ["季度", "每周", "上周", "本周", "排除", "不含", "除外"]
    ):
        raise DomainError(
            "当前不支持所问时间粒度或排除范围，请改问完整自然月和明确门店范围。", 422
        )
    periods = []
    for match in months:
        preceding = [y for y in years if y.end() <= match.start()]
        if not preceding:
            raise DomainError("请在本期月份前写明年份。", 422)
        periods.append((int(preceding[-1].group(1)), int(match.group(1))))
    if years[-1].start() > months[-1].start():
        raise DomainError("请将年份写在对应月份前面。", 422)
    yoy = any(word in question for word in ["同比", "去年", "上年"])
    mom = "环比" in question
    if yoy and mom:
        raise DomainError("一次请选择同比或环比，不要混用两种比较方式。", 422)
    if len(periods) == 2 and not any(
        word in question
        for word in ["比", "变化", "下降", "增长", "差", "为什么", "去年", "上年"]
    ):
        raise DomainError(
            "问题包含两个完整月份，请明确是本期与基期比较；当前不支持合并月份统计。",
            422,
        )
    year, month = periods[0]
    expected = (
        (year - 1, month)
        if yoy
        else ((year, month - 1) if month > 1 else (year - 1, 12))
    )
    baseline = periods[1] if len(periods) == 2 else expected
    if len(periods) == 2 and baseline >= periods[0]:
        raise DomainError(
            "请将本期写在前面、较早的基期写在后面，并明确跨年的年份。", 422
        )
    if (yoy or mom) and baseline != expected:
        raise DomainError("指定基期与同比或环比不一致，请核对比较月份和年份。", 422)
    compare = (
        len(periods) == 2
        or yoy
        or mom
        or any(
            w in question
            for w in ["相比", "对比", "比较", "上月", "下降", "增长", "变化", "为什么"]
        )
    )
    comparison = "yoy" if yoy else "mom" if mom or baseline == expected else "custom"
    start, end = month_range(year, month)
    ps, pe = month_range(*baseline)
    if (
        start < coverage_start
        or end > coverage_end
        or (compare and (ps < coverage_start or pe > coverage_end))
    ):
        label = "同比（去年同月）" if yoy else "所选比较"
        raise DomainError(
            f"数据未完整覆盖{label}期间 {ps} 至 {pe} 或本期 {start} 至 {end}。"
            f"当前覆盖 {coverage_start} 至 {coverage_end}，请补齐数据或调整月份。",
            422,
        )
    requested = [
        s["store_id"]
        for s in data["stores"]
        if s["name"] in question or s["store_id"] in question
    ]
    if len(requested) > 1:
        raise DomainError(
            "初版一次支持一个指定门店，或按地区分析全部授权门店。请调整范围。", 422
        )
    known_regions = {s["region"] for s in data["stores"]}
    regions = sorted(r for r in known_regions if r in question)
    for region in ["华东", "华南", "华北", "华中", "东北", "西北", "西南"]:
        if region in question and region not in known_regions:
            raise DomainError("所问地区不在当前数据版本中，请调整地区范围。", 422)
    if len(regions) > 1:
        raise DomainError(
            "当前一次支持一个地区；多地区合并或对比暂不支持，请选择一个地区或明确全部授权门店。",
            422,
        )
    region = regions[0] if regions else ""
    if (
        requested
        and region
        and not any(
            s["store_id"] == requested[0] and s["region"] == region
            for s in data["stores"]
        )
    ):
        raise DomainError("指定门店与地区不一致，请核对分析范围。", 422)
    if (
        "店" in question
        and not requested
        and not any(
            word in question for word in ["各门店", "全部门店", "所有门店", "门店排名"]
        )
    ):
        raise DomainError("请使用完整门店名称或门店编号，或明确分析各门店。", 422)
    for p in data["products"]:
        if (
            p["name"] in question
            or p["category"] in question
            or re.search(
                r"(?<![A-Za-z0-9_.-])"
                + re.escape(p["product_id"])
                + r"(?![A-Za-z0-9_.-])",
                question,
            )
        ):
            raise DomainError(
                "初版支持全部品类拆解，暂不支持按指定商品或品类筛选问题。请移除该筛选条件。",
                422,
            )
    return {
        "start": start,
        "end": end,
        "previous_start": ps,
        "previous_end": pe,
        "compare": compare,
        "comparison": comparison if compare else "none",
        "store": requested[0] if requested else "",
        "region": region,
        "metrics": metrics,
        "ranking": "排名" in question or "排行" in question,
        "plan_version": "monthly-v2",
    }


def metric_rows(current, previous, metric, identity):
    old = {row[identity]: row for row in previous} if previous is not None else {}
    rows = []
    for row in current:
        before = old.get(row[identity])
        # Missing categories represent zero activity, whereas empty stores have no average.
        prior = (
            before.get(metric)
            if before
            else (None if metric == "average" or previous is None else 0)
        )
        value = row[metric]
        delta = value - prior if value is not None and prior is not None else None
        rows.append({**row, "value": value, "previous": prior, "delta": delta})
    # Include categories which disappeared in the current period.
    for key in old.keys() - {row[identity] for row in current}:
        before = old[key]
        value = None if metric == "average" else 0
        prior = before[metric]
        rows.append(
            {
                identity: key,
                "name": before["name"],
                **dimension_metrics(0, 0, 0),
                "value": value,
                "previous": prior,
                "delta": value - prior
                if value is not None and prior is not None
                else None,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row.get("empty", False),
            row["value"] is None,
            -(row["value"] or 0),
            row[identity],
        ),
    )


def analyze(data, allowed, plan, version, metric_version=None, queried=None):
    metric_version = metric_version or METRIC_VERSION
    if queried is not None:
        cur, prev = queried
    else:
        cur = aggregate(
            data, allowed, plan["start"], plan["end"], plan["store"], plan["region"]
        )
        prev = (
            aggregate(
                data,
                allowed,
                plan["previous_start"],
                plan["previous_end"],
                plan["store"],
                plan["region"],
            )
            if plan["compare"]
            else None
        )
    metric_results, summaries = [], []
    for metric in plan.get("metrics", ["net"]):
        definition = METRICS[metric]
        value = None if cur["empty"] else cur[metric]
        previous = None if prev is None or prev["empty"] else prev[metric]
        delta = value - previous if value is not None and previous is not None else None
        rate = (
            round(delta / previous * 100, 2) if delta is not None and previous else None
        )
        store_rows = metric_rows(
            cur["stores"],
            prev["stores"] if prev is not None else None,
            metric,
            "store_id",
        )
        category_rows = metric_rows(
            cur["categories"],
            prev["categories"] if prev is not None else None,
            metric,
            "name",
        )
        # Category order counts overlap when an order contains several categories.
        category_additive = metric in ("net", "quantity")
        contributions = (
            store_rows
            if prev is not None and definition["additive"] and delta is not None
            else []
        )
        category_contributions = (
            category_rows
            if prev is not None and category_additive and delta is not None
            else []
        )
        if contributions:
            assert sum((row["delta"] or 0) for row in contributions) == delta
        if category_contributions:
            assert sum((row["delta"] or 0) for row in category_contributions) == delta
        minimum_drop = 100000 if metric == "net" else 0
        abnormal = (
            rate is not None and rate <= -15 and delta < 0 and -delta >= minimum_drop
        )
        rule = (
            "下降至少 15% 且下降额至少 1,000 元"
            if metric == "net"
            else "下降至少 15%（描述性变化提示）"
        )
        text = f"本期{definition['label']}：{metric_text(metric, value)}。"
        if delta is not None:
            movement = "减少" if delta < 0 else "增加" if delta > 0 else "不变，变化为"
            text += f"基期：{metric_text(metric, previous)}；较基期{movement} {metric_text(metric, abs(delta))}"
            text += (
                f"（{rate:+.2f}%）。"
                if rate is not None
                else "；基期为零，变化率不适用。"
            )
        elif prev is not None:
            text += "本期或基期无有效成交记录，不计算变化额和变化率。"
        if plan.get("ranking"):
            ranked = [
                row
                for row in store_rows
                if row["value"] is not None and not row.get("empty")
            ]
            text += (
                "门店排名（从高到低，前三）："
                + "；".join(
                    f"{row['name']} {metric_text(metric, row['value'])}"
                    for row in ranked[:3]
                )
                + "。"
                if ranked
                else "没有可排名的有效成交记录。"
            )
        summaries.append(text)
        metric_results.append(
            {
                "metric": metric,
                **definition,
                "current": value,
                "previous": previous,
                "delta": delta,
                "rate": rate,
                "rankings": store_rows,
                "category_values": category_rows,
                "contributions": contributions,
                "category_contributions": category_contributions,
                "abnormal": abnormal,
                "rule": rule,
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            [version, sorted(allowed), plan, metric_version], sort_keys=True
        ).encode()
    ).hexdigest()[:16]
    evidence = {
        "id": "EV-" + fingerprint[:8].upper(),
        "fingerprint": fingerprint,
        "version_id": version,
        "metric_version": metric_version,
        "scope": [s["store_id"] for s in cur["stores"]],
        "plan": plan,
        "unit": metric_results[0]["storage_unit"]
        if len(metric_results) == 1
        else "各指标分别计量，见 units",
        "units": {m["metric"]: m["storage_unit"] for m in metric_results},
        "current": cur,
        "previous": prev,
        "metric_results": metric_results,
    }
    primary = metric_results[0]
    abnormal = any(m["abnormal"] for m in metric_results)
    rules = "；".join(f"{m['label']}：{m['rule']}" for m in metric_results)
    limits = (
        "数据仅支持描述变化与贡献；缺少库存、客流和活动记录，不能据此确认业务原因。异常规则："
        + rules
        + "。"
    )
    if "average" in plan.get("metrics", []):
        limits += (
            "客单价由总净销售额除以总有效订单数计算，门店客单价不可相加作为总体贡献。"
        )
    if "orders" in plan.get("metrics", []):
        limits += "跨品类订单会在各品类分别计数，品类订单数不可相加作为总订单数。"
    return {
        "current": cur,
        "previous": prev,
        "metric_results": metric_results,
        "metric_version": metric_version,
        "delta": primary["delta"],
        "rate": primary["rate"],
        "contributions": primary["contributions"],
        "category_contributions": primary["category_contributions"],
        "abnormal": abnormal,
        "summary": "所选范围没有有效成交记录，无法形成经营结论。"
        if cur["empty"]
        else " ".join(summaries),
        "evidence": evidence,
        "limits": limits,
        "recommendation": "优先复核下降门店的商品供给、客流与促销记录，再决定是否调整经营策略。"
        if abnormal
        else "持续跟踪门店与品类表现，结合实际业务记录复核变化。",
    }
