"""Strict write contracts; unknown fields and implicit type coercions are rejected."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .domain import FIELDS, DomainError


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Empty(Contract):
    pass


class Login(Contract):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(max_length=1024)


class Register(Contract):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,40}$")
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=1024)
    confirm_password: str = Field(max_length=1024)


class Password(Contract):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class Import(Contract):
    source: str = Field(min_length=1, max_length=300)
    name: str = Field(default="经营数据导入", max_length=120)
    files: dict[str, str]
    mapping: dict[str, dict[str, str]] = Field(default_factory=dict)


class Publish(Contract):
    coverage_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    coverage_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    snapshot_at: str = Field(max_length=50)
    accept_warnings: bool = False


class Task(Contract):
    question: str = Field(min_length=1, max_length=2000)
    version_id: str = Field(default="", max_length=80)
    retry_of: str = Field(default="", max_length=80)


class Report(Contract):
    task_id: str = Field(min_length=1, max_length=80)


Role = Literal["admin", "analyst", "manager", "store"]


class UserUpdate(Contract):
    roles: list[Role] = Field(min_length=1, max_length=4)
    stores: list[str] = Field(max_length=1000)
    enabled: bool = True


class UserCreate(UserUpdate):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,40}$")
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=1024)


class Reset(Contract):
    password: str = Field(min_length=12, max_length=1024)


class Edit(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=6000)
    recommendation: str | None = Field(default=None, max_length=6000)


class Review(Contract):
    comment: str = Field(min_length=1, max_length=2000)


class Feedback(Contract):
    state: Literal["待评估", "已采纳", "暂缓", "未采纳"]
    owner: str = Field(default="", max_length=100)
    comment: str = Field(default="", max_length=2000)
    suggestion: str = Field(default="", max_length=1000)
    due: str = Field(default="", max_length=10)
    action: str = Field(default="", max_length=2000)
    review_metric: str = Field(default="", max_length=300)


async def body(request, model):
    try:
        result = model.model_validate(await request.json()).model_dump(
            exclude_none=True
        )
    except ValidationError as exc:
        detail = "；".join(
            ".".join(map(str, e["loc"])) + ": " + e["type"] for e in exc.errors()[:8]
        )
        error = DomainError("输入格式无效，请检查字段类型、长度和必填项：" + detail)
        if model is ModelConfig:
            raise error from None
        raise error from exc
    if model is Import:
        if set(result["files"]) - set(FIELDS) or set(result["mapping"]) - set(FIELDS):
            raise DomainError("文件或映射含未知表名")
        for table, mapping in result["mapping"].items():
            if set(mapping) - set(FIELDS[table]):
                raise DomainError("字段映射含未知字段：" + table)
    return result


class Workbook(Contract):
    content: str


class ModelConfig(Contract):
    provider: Literal["rules", "openai-compatible"]
    base_url: str = Field(max_length=2048)
    model_name: str = Field(max_length=200)
    max_output: int = Field(ge=100, le=2000)
    daily_budget: int = Field(ge=1, le=1000000000)
    api_key: str = Field(default="", max_length=4096, repr=False)
    clear_api_key: bool = False
    revision: int = Field(ge=0)
