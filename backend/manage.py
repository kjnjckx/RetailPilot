"""Local one-time account setup for a new RetailPilot database."""

import argparse
import getpass
import os
import re
import secrets
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import storage as db


def init_admin(identifier: str, name: str, password: str):
    db.initialize()
    if db.all_objects("users"):
        raise ValueError("数据库中已有账号；请由现有管理员在页面创建用户")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", identifier):
        raise ValueError("账号需为 3–40 位字母、数字、下划线、点或连字符")
    if not name.strip() or len(name) > 80 or len(password) < 12:
        raise ValueError("姓名不可为空，密码至少 12 位")
    salt = secrets.token_hex(16)
    db.put(
        "users",
        {
            "id": identifier,
            "name": name.strip(),
            "roles": ["admin"],
            "stores": [],
            "salt": salt,
            "password": db.password_hash(password, salt),
        },
    )


def _model_key_path(database):
    database = Path(database)
    return database.with_name(database.name + ".model-key")


def _paired_model_key(con, database):
    """Read the matching key without placing any decrypted secret in a backup."""
    if not con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_config'"
    ).fetchone():
        return None
    encrypted = con.execute(
        "SELECT encrypted_api_key FROM model_config WHERE encrypted_api_key<>''"
    ).fetchall()
    if not encrypted:
        return None
    try:
        descriptor = os.open(
            _model_key_path(database), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("模型加密密钥文件权限无效")
            key = stream.read(128)
        cipher = Fernet(key)
        for row in encrypted:
            cipher.decrypt(row[0].encode())
        return key
    except (OSError, ValueError, InvalidToken) as exc:
        raise ValueError(
            "模型加密密钥文件缺失或与数据库不匹配，无法备份或恢复"
        ) from exc


def _create_private_file(path, content=b""):
    """Reserve a new file with private permissions; never overwrite a backup."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def backup(destination):
    path = Path(destination).resolve()
    key_path = _model_key_path(path)
    if (
        path == db.DB_PATH.resolve()
        or path.exists()
        or key_path.exists()
        or key_path.is_symlink()
    ):
        raise ValueError("备份路径必须是新的文件")
    if not db.DB_PATH.exists():
        raise ValueError("数据库不存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    _create_private_file(path)
    key_created = False
    try:
        with db.connection() as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份完整性检查失败")
            key = _paired_model_key(target, db.DB_PATH)
        if key:
            _create_private_file(key_path, key)
            key_created = True
        os.chmod(path, 0o600)
    except Exception:
        path.unlink(missing_ok=True)
        if key_created:
            key_path.unlink(missing_ok=True)
        raise
    return path


def restore(source, stopped=False):
    if not stopped:
        raise ValueError("请先停止全部服务进程，并传入 --service-stopped")
    source = Path(source).resolve()
    if source == db.DB_PATH.resolve() or not source.is_file():
        raise ValueError("请提供有效备份文件")
    temporary = db.DB_PATH.with_suffix(".restore.tmp")
    temporary_key = _model_key_path(temporary)
    if (
        temporary.exists()
        or temporary.is_symlink()
        or temporary_key.exists()
        or temporary_key.is_symlink()
    ):
        raise ValueError("存在未处理的恢复临时文件")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份文件损坏")
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'"
        ).fetchone():
            raise ValueError("不是 RetailPilot 数据库")
        if con.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise ValueError("备份版本比当前程序新")
        key = _paired_model_key(con, source)
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _create_private_file(temporary)
        temporary_key_created = False
        try:
            with closing(sqlite3.connect(temporary)) as target:
                con.backup(target)
            if key:
                _create_private_file(temporary_key, key)
                temporary_key_created = True
            if db.DB_PATH.exists():
                backup(
                    db.DB_PATH.with_name(
                        db.DB_PATH.name + ".before-restore-" + db.uid("BK")
                    )
                )
            destination_key = _model_key_path(db.DB_PATH)
            previous_key = (
                destination_key.read_bytes() if destination_key.exists() else None
            )
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(db.DB_PATH) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            if key:
                os.replace(temporary_key, destination_key)
                temporary_key_created = False
            else:
                destination_key.unlink(missing_ok=True)
            try:
                os.replace(temporary, db.DB_PATH)
            except Exception:
                if previous_key is not None:
                    _create_private_file(temporary_key, previous_key)
                    temporary_key_created = True
                    os.replace(temporary_key, destination_key)
                    temporary_key_created = False
                else:
                    destination_key.unlink(missing_ok=True)
                raise
            os.chmod(db.DB_PATH, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
            if temporary_key_created:
                temporary_key.unlink(missing_ok=True)
    db.initialize()
    with db.connection() as con:
        con.execute("DELETE FROM sessions")


def main():
    parser = argparse.ArgumentParser(description="RetailPilot 本地账号与备份管理")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("init-admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument(
        "--if-empty", action="store_true", help="已有账号时跳过初始化，不修改现有账号"
    )
    back = sub.add_parser("backup")
    back.add_argument("path")
    recover = sub.add_parser("restore")
    recover.add_argument("path")
    recover.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            print("备份完成：", backup(args.path))
            return
        if args.command == "restore":
            restore(args.path, args.service_stopped)
            print("恢复完成，请重启服务并重新登录。")
            return
        if args.if_empty:
            db.initialize()
            if db.all_objects("users"):
                print("已有账号，跳过管理员初始化。")
                return
            print(f"首次使用：请为管理员 {args.username} 设置密码。")
            if not os.isatty(0):
                raise ValueError("首次创建管理员需要交互终端，请在终端中运行启动命令")
        password = getpass.getpass("设置密码（至少 12 位）：")
        confirm = getpass.getpass("再次输入密码：")
        if password != confirm:
            raise ValueError("两次密码不一致")
        init_admin(args.username, args.name, password)
    except (ValueError, sqlite3.Error, OSError) as exc:
        parser.error(str(exc))
    print(f"管理员 {args.username} 已创建。现在可以启动服务并登录。")


if __name__ == "__main__":
    main()
