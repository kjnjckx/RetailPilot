"""Bounded XLSX values import. Formulas/macros/external links are rejected."""

import base64
import binascii
import csv
import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile

from .domain import FIELDS, DomainError

NS = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def convert(encoded):
    if len(encoded) > 28 * 1024 * 1024:
        raise DomainError("Excel 文件不能超过 20 MB", 413)
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 20 * 1024 * 1024:
            raise DomainError("Excel 文件不能超过 20 MB", 413)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if (
                len(infos) > 1000
                or sum(i.file_size for i in infos) > 80 * 1024 * 1024
                or any(i.flag_bits & 1 for i in infos)
            ):
                raise DomainError("工作簿过大或加密，请使用未加密四表文件")
            if any(
                "vbaproject" in i.filename.lower()
                or "externallinks" in i.filename.lower()
                for i in infos
            ):
                raise DomainError("不支持宏或外部链接，请导出仅包含值的工作簿")

            def read(name):
                body = z.read(name)
                if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                    raise ValueError()
                return ET.fromstring(body)

            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings = [
                    "".join(x.itertext())
                    for x in read("xl/sharedStrings.xml").findall("x:si", NS)
                ]
            relationships = {
                r.attrib["Id"]: r.attrib["Target"]
                for r in read("xl/_rels/workbook.xml.rels")
                if r.attrib.get("TargetMode") != "External"
            }
            files = {}
            for sheet in read("xl/workbook.xml").findall("x:sheets/x:sheet", NS):
                name = sheet.attrib["name"]
                if name not in FIELDS:
                    continue
                target = relationships[sheet.attrib["{" + NS["r"] + "}id"]]
                path = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath("xl/" + target)
                )
                if not path.startswith("xl/"):
                    raise ValueError()
                root = read(path)
                out = io.StringIO()
                writer = csv.writer(out)
                rows = root.findall("x:sheetData/x:row", NS)
                if len(rows) > 100001:
                    raise DomainError("每张表最多 100,000 条业务记录")
                for row in rows:
                    values = {}
                    for cell in row.findall("x:c", NS):
                        if cell.find("x:f", NS) is not None:
                            raise DomainError(f"{name} 表含公式，请粘贴为值后上传")
                        ref = cell.attrib.get("r", "")
                        col = 0
                        for ch in ref:
                            if not ch.isalpha():
                                break
                            col = col * 26 + ord(ch.upper()) - 64
                        if col < 1 or col > 100:
                            raise DomainError("工作表最多支持 100 列")
                        value = cell.findtext("x:v", default="", namespaces=NS)
                        if cell.attrib.get("t") == "s":
                            value = strings[int(value)]
                        elif cell.attrib.get("t") == "inlineStr":
                            value = "".join(cell.find("x:is", NS).itertext())
                        elif cell.attrib.get("t") == "e":
                            raise DomainError(f"{name} 表含错误单元格")
                        if len(value) > 65536:
                            raise DomainError("Excel 单字段最多 65,536 字符")
                        values[col] = value
                    if values:
                        writer.writerow(
                            [values.get(i, "") for i in range(1, max(values) + 1)]
                        )
                files[name] = out.getvalue()
            if set(files) != set(FIELDS):
                raise DomainError(
                    "请使用 stores、products、orders、items 四张工作表；成交时间需填带 +08:00 的文本"
                )
            if sum(len(v.encode()) for v in files.values()) > 20 * 1024 * 1024:
                raise DomainError("四表转换后总量超过 20 MB", 413)
            return files
    except DomainError:
        raise
    except (
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        ET.ParseError,
        zipfile.BadZipFile,
        RuntimeError,
        binascii.Error,
    ) as exc:
        raise DomainError(
            "工作簿损坏或格式不支持，请使用标准 .xlsx（成交时间以 ISO 文本填写）"
        ) from exc
