"""Immutable version aggregates. SQL parameters are always server-authorized scopes."""

import copy
import functools
from datetime import date, timedelta

from . import storage as db
from .domain import METRIC_VERSION, DomainError, dimension_metrics, scope_stores


def aggregate_version(version, allowed, start, end, store="", region=""):
    try:
        duration = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        if duration < 1 or duration > 3660:
            raise ValueError()
    except ValueError as exc:
        raise DomainError("请选择有效日期范围（最多十年）") from exc
    stores = scope_stores({"stores": version["store_meta"]}, allowed, store, region)
    # Authorization is rechecked before every cache lookup, never inferred from a hit.
    result = cached(
        str(db.DB_PATH.resolve()),
        version["id"],
        METRIC_VERSION,
        tuple(sorted(s["store_id"] for s in stores)),
        start,
        end,
    )
    return copy.deepcopy(result)


@functools.lru_cache(maxsize=128)
def cached(database, version_id, metric_version, scope, start, end):
    v = db.get("versions", version_id, full=False)
    stores = [s for s in v["store_meta"] if s["store_id"] in scope]
    if not scope:
        groups, categories = [], []
    else:
        marks = ",".join("?" for _ in scope)
        where = f"o.version=? AND o.store IN ({marks}) AND o.date BETWEEN ? AND ? AND o.status!='已取消'"
        args = [version_id, *scope, start, end]
        with db.connection() as con:
            # Summarize each order first so multiple lines never multiply order counts.
            groups = con.execute(
                "SELECT o.store,o.date,count(*) AS orders,sum(i.net) AS net,sum(i.quantity) AS quantity FROM v_orders o JOIN (SELECT version,order_id,sum(net) net,sum(quantity) quantity FROM v_items WHERE version=? GROUP BY version,order_id) i ON i.version=o.version AND i.order_id=o.id WHERE "
                + where
                + " GROUP BY o.store,o.date",
                [version_id, *args],
            ).fetchall()
            categories = con.execute(
                "SELECT p.category AS name,sum(i.net) AS net,sum(i.quantity) AS quantity,count(DISTINCT o.id) AS orders FROM v_orders o JOIN v_items i ON i.version=o.version AND i.order_id=o.id JOIN v_products p ON p.version=i.version AND p.id=i.product WHERE "
                + where
                + " GROUP BY p.category ORDER BY net DESC,p.category",
                args,
            ).fetchall()
    bystore = {s["store_id"]: [0, 0, 0] for s in stores}
    daily = {}
    total = [0, 0, 0]
    for row in groups:
        values = [row["net"], row["orders"], row["quantity"]]
        d = daily.setdefault(row["date"], [0, 0, 0])
        for i, value in enumerate(values):
            total[i] += value
            bystore[row["store"]][i] += value
            d[i] += value
    days = [
        (date.fromisoformat(start) + timedelta(days=i)).isoformat()
        for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    ]
    return {
        **dimension_metrics(*total),
        "stores": sorted(
            [{**s, **dimension_metrics(*bystore[s["store_id"]])} for s in stores],
            key=lambda s: -s["net"],
        ),
        "daily": [
            {
                "date": day,
                **{
                    k: v
                    for k, v in dimension_metrics(*daily.get(day, [0, 0, 0])).items()
                    if k != "empty"
                },
            }
            for day in days
        ],
        "categories": [
            {
                "name": r["name"],
                **dimension_metrics(r["net"], r["orders"], r["quantity"]),
            }
            for r in categories
        ],
    }
