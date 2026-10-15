import hashlib
import time

from . import storage as db
from .domain import DomainError


def user(request):
    token = request.cookies.get("retailpilot_session", "")
    with db.connection() as con:
        row = con.execute(
            "SELECT user_id FROM sessions WHERE token=? AND expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
    u = db.get("users", row[0]) if row else None
    if not u or not u.get("enabled", True):
        raise DomainError("请先登录", 401)
    return u


def public_user(u):
    return {k: v for k, v in u.items() if k not in ("password", "salt")}


def role(u, *roles):
    if not set(u["roles"]).intersection(roles):
        raise DomainError("当前角色没有此操作权限", 403)


def can_read_data(u):
    return bool(u and set(u["roles"]).intersection(["analyst", "manager", "store"]))


def require_data_role(u):
    if not can_read_data(u):
        raise DomainError("读取经营数据需要分析、管理或门店业务角色", 403)


def visible(u, obj):
    return set(obj.get("scope", [])).issubset(u["stores"]) and bool(obj.get("scope"))


def object_for(u, kind, id):
    if kind in ("tasks", "reports") and not can_read_data(u):
        raise DomainError("记录不存在或没有访问权限", 404)
    obj = db.get(kind, id)
    if (
        not obj
        or not (visible(u, obj) or (kind == "imports" and "admin" in u["roles"]))
        or (
            kind == "reports"
            and not set(u["roles"]).intersection(["analyst", "manager"])
            and obj["status"] != "approved"
        )
    ):
        raise DomainError("记录不存在或没有访问权限", 404)
    return obj
