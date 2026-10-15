import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
import { readFileSync } from "node:fs";
const result = ts.transpileModule(readFileSync("src/lib.ts", "utf8"), {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
});
const lib = await import(
  "data:text/javascript;base64," +
    Buffer.from(result.outputText).toString("base64")
);
test("CSV headers preserve quoted commas, escaped quotes, BOM and embedded newline", () => {
  assert.deepEqual(
    lib.csvHeader('\uFEFFstore_id,"门店,名称","地""区"\r\nA,甲,东'),
    ["store_id", "门店,名称", '地"区'],
  );
  assert.deepEqual(lib.csvHeader('id,"multi\nline",region\nA,x,东'), [
    "id",
    "multi\nline",
    "region",
  ]);
  assert.throws(() => lib.csvHeader('id,"unterminated'), /引号/);
});
test("metric formatting keeps counts and cents in separate units", () => {
  assert.equal(
    lib.formatMetric({ storage_unit: "笔", unit: "笔" }, 12),
    "12 笔",
  );
  assert.equal(
    lib.formatMetric({ storage_unit: "人民币分", unit: "元" }, 1234),
    "¥12.34",
  );
  assert.equal(
    lib.formatMetric({ storage_unit: "件", unit: "件" }, null),
    "不适用",
  );
});
test("timestamps and complete months remain correct across year and Shanghai midnight", () => {
  const formatted = lib.when("2026-10-01T16:00:00Z");
  assert.match(formatted, /2026/);
  assert.match(formatted, /10\/02/);
  assert.equal(
    lib.latestCompleteMonth("2025-12-01", "2026-01-30"),
    "2025 年 12 月",
  );
  assert.equal(lib.latestCompleteMonth("2026-01-01", "2026-01-30"), "");
  assert.equal(lib.when(""), "—");
});
test("navigation URLs preserve version and remove obsolete selection values", () => {
  let url = new URL("http://test.local/?page=analysis&version=V1&task=T1");
  globalThis.location = {
    get href() {
      return url.href;
    },
    get search() {
      return url.search;
    },
  };
  globalThis.history = {
    replaceState(_a, _b, next) {
      url = new URL(next);
    },
    pushState(_a, _b, next) {
      url = new URL(next);
    },
  };
  lib.writeLocation({ page: "reports", task: "", report: "R1" }, true);
  assert.equal(lib.readLocation("version"), "V1");
  assert.equal(lib.readLocation("task"), "");
  assert.equal(lib.readLocation("report"), "R1");
});
