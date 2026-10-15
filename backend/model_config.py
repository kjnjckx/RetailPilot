"""Server-managed model settings with encrypted credentials and revision checks."""

import ipaddress
import os
import stat
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken

from . import storage as db
from .domain import DomainError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
CONFIG_COLUMNS = (
    "provider,base_url,model_name,max_output,daily_budget,encrypted_api_key,"
    "revision,updated_at"
)


@dataclass(frozen=True)
class RuntimeConfig:
    provider: str
    base_url: str
    model_name: str
    max_output: int
    daily_budget: int
    api_key: str = field(default="", repr=False)
    has_api_key: bool = False
    source: str = "default"
    revision: int = 0
    updated_at: str | None = None

    @property
    def enabled(self):
        return self.provider == "openai-compatible"

    def public(self):
        value = {
            "provider": self.provider,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "max_output": self.max_output,
            "daily_budget": self.daily_budget,
            "has_api_key": self.has_api_key,
            "source": self.source,
            "revision": self.revision,
            "enabled": self.enabled,
        }
        if self.updated_at:
            value["updated_at"] = self.updated_at
        return value


def validate_base_url(value):
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise DomainError("模型地址格式无效")
    if "\\" in value:
        raise DomainError("模型地址格式无效")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise DomainError("模型地址格式无效") from None
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or (port is not None and not 1 <= port <= 65535)
        or "%" in parsed.netloc
    ):
        raise DomainError("模型地址不可包含账号、查询参数或片段")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and host in LOCAL_HOSTS
    ):
        raise DomainError("模型地址须使用 HTTPS，或本机 HTTP")
    if host not in LOCAL_HOSTS:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".internal", ".localhost")):
                raise DomainError("模型地址须使用公开服务地址，或本机地址") from None
        else:
            if not address.is_global:
                raise DomainError("模型地址不可使用内网或保留地址")
    return urlunsplit(
        (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
    )


def key_path():
    return db.DB_PATH.with_name(db.DB_PATH.name + ".model-key")


def _new_key_file(path):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(Fernet.generate_key())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _cipher(con, create=False):
    path = key_path()
    try:
        if not path.exists() and create:
            row = con.execute(
                "SELECT encrypted_api_key FROM model_config WHERE id=1"
            ).fetchone()
            if row and row[0]:
                raise DomainError("模型密钥加密文件缺失，请恢复配套备份或先清除旧密钥")
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(Fernet.generate_key())
                    handle.flush()
                    os.fsync(handle.fileno())
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise DomainError("模型密钥加密文件权限无效，请仅允许服务账号读取")
            material = handle.read(128)
        return Fernet(material)
    except (DomainError, OSError, ValueError) as exc:
        # Explicitly clearing the old credential is required before replacing an
        # unavailable key file. Never invalidate ciphertext still stored in the DB.
        if create:
            row = con.execute(
                "SELECT encrypted_api_key FROM model_config WHERE id=1"
            ).fetchone()
            if not row or not row[0]:
                try:
                    _new_key_file(path)
                    return _cipher(con)
                except (OSError, ValueError):
                    pass
        if isinstance(exc, DomainError):
            raise
        raise DomainError(
            "模型密钥加密文件不可用，请恢复配套备份或先清除旧密钥"
        ) from None


def _decrypt(con, ciphertext):
    if not ciphertext:
        return ""
    try:
        return _cipher(con).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError):
        raise DomainError("模型密钥无法解密，请恢复配套备份或先清除旧密钥") from None


def _environment(strict=False):
    names = ["PROVIDER", "BASE_URL", "NAME", "MAX_OUTPUT", "DAILY_BUDGET", "API_KEY"]
    source = (
        "environment"
        if any("RETAILPILOT_MODEL_" + name in os.environ for name in names)
        else "default"
    )
    provider = os.getenv("RETAILPILOT_MODEL_PROVIDER", "rules")
    base = os.getenv("RETAILPILOT_MODEL_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("RETAILPILOT_MODEL_NAME", "")

    def budget(name, default, minimum, maximum):
        try:
            value = int(os.getenv("RETAILPILOT_MODEL_" + name, str(default)))
        except ValueError:
            if strict and provider != "rules":
                raise DomainError("模型预算配置无效") from None
            return default
        return (
            value
            if (strict and provider != "rules") or minimum <= value <= maximum
            else default
        )

    if not strict:
        # A malformed legacy environment must not prevent an administrator from
        # opening settings and replacing it with valid saved configuration.
        provider = provider if provider in {"rules", "openai-compatible"} else "rules"
        try:
            base = validate_base_url(base)
        except DomainError:
            base = ""
    try:
        return RuntimeConfig(
            provider=provider,
            base_url=base.rstrip("/"),
            model_name=model,
            max_output=budget("MAX_OUTPUT", 800, 100, 2000),
            daily_budget=budget("DAILY_BUDGET", 100000, 1, 1000000000),
            api_key=os.getenv("RETAILPILOT_MODEL_API_KEY", ""),
            has_api_key=bool(os.getenv("RETAILPILOT_MODEL_API_KEY", "")),
            source=source,
        )
    except ValueError:
        raise DomainError("模型预算配置无效") from None


def _resolved(con, decrypt):
    row = con.execute(
        "SELECT " + CONFIG_COLUMNS + " FROM model_config WHERE id=1"
    ).fetchone()
    if not row:
        return _environment(strict=decrypt)
    return RuntimeConfig(
        provider=row["provider"],
        base_url=row["base_url"],
        model_name=row["model_name"],
        max_output=row["max_output"],
        daily_budget=row["daily_budget"],
        api_key=_decrypt(con, row["encrypted_api_key"])
        if decrypt and row["provider"] == "openai-compatible"
        else "",
        has_api_key=bool(row["encrypted_api_key"]),
        source="database",
        revision=row["revision"],
        updated_at=row["updated_at"],
    )


def resolve_config(decrypt=True):
    with db.connection() as con:
        if decrypt:
            con.execute("BEGIN IMMEDIATE")
        return _resolved(con, decrypt)


def public_config():
    return resolve_config(decrypt=False).public()


def _draft(con, payload):
    current = _resolved(con, decrypt=False)
    if payload["revision"] != current.revision:
        raise DomainError("模型配置已被其他管理员更新，请刷新后重试", 409)
    raw_base = payload["base_url"]
    raw_model = payload["model_name"]
    raw_key = payload.get("api_key", "")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_base):
        raise DomainError("模型地址格式无效")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_key):
        raise DomainError("服务密钥格式无效")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_model):
        raise DomainError("模型名称格式无效")
    base = raw_base.strip()
    model = raw_model.strip()
    replacement = raw_key.strip()
    clear = payload.get("clear_api_key", False)
    if clear and replacement:
        raise DomainError("清除密钥与替换密钥不可同时选择")
    if any(ord(char) < 33 or ord(char) == 127 for char in replacement):
        raise DomainError("服务密钥格式无效")
    if any(ord(char) < 32 or ord(char) == 127 for char in model):
        raise DomainError("模型名称格式无效")
    base = validate_base_url(base) if base else ""
    old_base = current.base_url
    if old_base:
        try:
            old_base = validate_base_url(old_base)
        except DomainError:
            old_base = current.base_url
    if current.has_api_key and base != old_base and not replacement and not clear:
        raise DomainError("修改服务地址时请重新填写对应密钥，或清除旧密钥")
    ciphertext = ""
    if replacement:
        key = replacement
    elif clear:
        key = ""
    elif current.source == "database":
        row = con.execute(
            "SELECT encrypted_api_key FROM model_config WHERE id=1"
        ).fetchone()
        ciphertext = row[0]
        # Disabling can retain an encrypted credential even if its sidecar is unavailable.
        key = (
            _decrypt(con, ciphertext)
            if payload["provider"] == "openai-compatible"
            else ""
        )
    else:
        key = current.api_key
    draft = RuntimeConfig(
        provider=payload["provider"],
        base_url=base,
        model_name=model,
        max_output=payload["max_output"],
        daily_budget=payload["daily_budget"],
        api_key=key,
        has_api_key=bool(key or ciphertext),
        source="database",
        revision=current.revision,
    )
    validate_runtime(draft)
    return draft, ciphertext


def validate_runtime(config):
    if config.provider not in {"rules", "openai-compatible"}:
        raise DomainError("模型提供方配置无效")
    if (
        not 100 <= config.max_output <= 2000
        or not 1 <= config.daily_budget <= 1000000000
    ):
        raise DomainError("模型预算配置无效")
    if config.enabled:
        validate_base_url(config.base_url)
        if not config.model_name or not config.api_key:
            raise DomainError("启用模型需要填写服务地址、模型名称和服务密钥")
        if any(ord(char) < 33 or ord(char) == 127 for char in config.api_key):
            raise DomainError("服务密钥格式无效")


def draft_config(payload):
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        draft, _ = _draft(con, payload)
    return draft


def save_config(payload, actor):
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        draft, ciphertext = _draft(con, payload)
        if draft.api_key:
            ciphertext = (
                _cipher(con, create=True).encrypt(draft.api_key.encode()).decode()
            )
        updated = db.now()
        revision = draft.revision + 1
        con.execute(
            "INSERT INTO model_config(id,"
            + CONFIG_COLUMNS
            + ") VALUES(1,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET provider=excluded.provider,base_url=excluded.base_url,"
            "model_name=excluded.model_name,max_output=excluded.max_output,"
            "daily_budget=excluded.daily_budget,encrypted_api_key=excluded.encrypted_api_key,"
            "revision=excluded.revision,updated_at=excluded.updated_at",
            (
                draft.provider,
                draft.base_url,
                draft.model_name,
                draft.max_output,
                draft.daily_budget,
                ciphertext,
                revision,
                updated,
            ),
        )
        db.save(
            con,
            "audit",
            {
                "id": db.uid("AU"),
                "at": updated,
                "actor": actor["name"],
                "user_id": actor["id"],
                "action": "更新模型配置",
                "target": "模型配置",
            },
        )
        row = _resolved(con, decrypt=False)
    return row.public()
