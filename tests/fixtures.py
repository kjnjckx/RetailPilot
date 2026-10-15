"""Deterministic CSV fixture for tests only."""

import csv
import io

from backend.domain import FIELDS


def metric_data():
    """Small hand-countable snapshot; money is already in cents."""
    stores = [
        {"store_id": "A", "name": "甲门店", "region": "华东"},
        {"store_id": "B", "name": "乙门店", "region": "华南"},
        {"store_id": "C", "name": "空门店", "region": "华东"},
    ]
    products = [{"product_id": "P", "name": "商品", "category": "日用"}]
    orders, items = [], []
    for index, (day, store, amount, quantity) in enumerate(
        [
            ("2026-07-01", "A", 4000, 1),
            ("2026-07-01", "B", 10000, 2),
            ("2026-08-01", "A", 3000, 4),
            ("2026-08-01", "B", 1000, 1),
            ("2026-08-02", "B", 1000, 1),
        ]
    ):
        oid = f"O{index}"
        orders.append(
            {
                "order_id": oid,
                "store_id": store,
                "date": day,
                "paid_at": day + "T12:00:00+08:00",
                "status": "已支付",
                "paid_amount": amount,
                "refund_amount": 0,
            }
        )
        items.append(
            {
                "order_id": oid,
                "line_id": "1",
                "product_id": "P",
                "paid_amount": amount,
                "refund_amount": 0,
                "quantity": quantity,
                "returned_quantity": 0,
            }
        )
    return {"stores": stores, "products": products, "orders": orders, "items": items}


def demo_files():
    stores = [
        {"store_id": "SH01", "name": "上海静安店", "region": "华东"},
        {"store_id": "HZ01", "name": "杭州西湖店", "region": "华东"},
        {"store_id": "SZ01", "name": "深圳南山店", "region": "华南"},
        {"store_id": "GZ01", "name": "广州天河店", "region": "华南"},
    ]
    products = [
        {"product_id": "P01", "name": "甄选生活用品", "category": "家居日用"},
        {"product_id": "P02", "name": "轻食与饮品", "category": "食品饮料"},
        {"product_id": "P03", "name": "日常护理套装", "category": "个护美妆"},
        {"product_id": "P04", "name": "城市休闲服饰", "category": "服饰配件"},
    ]
    orders, items = [], []
    targets = {
        7: [6000000, 4000000, 4400000, 3600000],
        8: [4500000, 3500000, 5000000, 4200000],
    }
    for month, amounts in targets.items():
        for sidx, total in enumerate(amounts):
            weights = [72 + ((d * 17 + sidx * 11) % 61) for d in range(1, 32)]
            allocated = [total * w // sum(weights) for w in weights]
            allocated[-1] += total - sum(allocated)
            for day, amount in enumerate(allocated, 1):
                for j in range(4):
                    val = amount // 4 + (amount % 4 if j == 3 else 0)
                    oid = f"D{month:02}{sidx}{day:02}{j}"
                    refund = 300 if day % 9 == 0 else 0
                    paid = val + refund
                    orders.append(
                        {
                            "order_id": oid,
                            "store_id": stores[sidx]["store_id"],
                            "paid_at": f"2026-{month:02}-{day:02}T12:00:00+08:00",
                            "status": "部分退款" if refund else "已支付",
                            "paid_amount": f"{paid / 100:.2f}",
                            "refund_amount": f"{refund / 100:.2f}",
                        }
                    )
                    items.append(
                        {
                            "order_id": oid,
                            "line_id": "1",
                            "product_id": products[(j + sidx) % 4]["product_id"],
                            "quantity": str(3 + day % 6),
                            "returned_quantity": "0",
                            "paid_amount": f"{paid / 100:.2f}",
                            "refund_amount": f"{refund / 100:.2f}",
                        }
                    )
    result = {}
    for table, rows in [
        ("stores", stores),
        ("products", products),
        ("orders", orders),
        ("items", items),
    ]:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=FIELDS[table])
        writer.writeheader()
        writer.writerows(rows)
        result[table] = out.getvalue()
    return result
