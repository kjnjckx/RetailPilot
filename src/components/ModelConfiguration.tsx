import React, { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  PlugZap,
  Save,
  Settings2,
} from "lucide-react";
import { when } from "../lib";

export type ModelConfig = {
  provider: "rules" | "openai-compatible";
  base_url: string;
  model_name: string;
  max_output: number;
  daily_budget: number;
  has_api_key: boolean;
  source: "database" | "environment" | "default";
  revision: number;
  updated_at?: string;
  enabled: boolean;
};

type Draft = {
  provider: ModelConfig["provider"];
  base_url: string;
  model_name: string;
  max_output: string;
  daily_budget: string;
  api_key: string;
  clear_api_key: boolean;
};
type ConnectionResult = { ok: boolean; message: string; model_name: string };

class ModelRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function requestModel<T>(
  path: string,
  signal: AbortSignal,
  body?: Record<string, unknown>,
): Promise<T> {
  const response = await fetch("/api" + path, {
    signal,
    method: body ? "POST" : "GET",
    headers: body
      ? {
          "Content-Type": "application/json",
          "X-Requested-With": "RetailPilot",
        }
      : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response
    .json()
    .catch(() => ({ error: "服务暂时不可用，请稍后重试" }));
  if (response.status === 401)
    window.dispatchEvent(new Event("retailpilot:unauthorized"));
  if (!response.ok)
    throw new ModelRequestError(
      data.error || "请求失败，请稍后重试",
      response.status,
    );
  return data;
}

const fromConfig = (config: ModelConfig): Draft => ({
  provider: config.provider,
  base_url: config.base_url,
  model_name: config.model_name,
  max_output: String(config.max_output),
  daily_budget: String(config.daily_budget),
  api_key: "",
  clear_api_key: false,
});

export default function ModelConfiguration({
  notify,
  onChanged,
}: {
  notify: (message: string) => void;
  onChanged: (config: ModelConfig) => void;
}) {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "test" | "">("load");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");
  const [testResult, setTestResult] = useState<ConnectionResult | null>(null);
  const operation = useRef<AbortController | null>(null);
  const dirty =
    !!config &&
    !!draft &&
    JSON.stringify(draft) !== JSON.stringify(fromConfig(config));
  const changedDestination =
    !!config?.has_api_key &&
    !!draft &&
    draft.base_url.trim().replace(/\/$/, "") !==
      config.base_url.replace(/\/$/, "");

  async function load() {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy("load");
    setError("");
    try {
      const next = await requestModel<ModelConfig>(
        "/model-config",
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setConfig(next);
      setDraft(fromConfig(next));
      setConflict(false);
      setSavedMessage("");
      setTestResult(null);
      onChanged(next);
    } catch (failure) {
      if (!controller.signal.aborted)
        setError(
          failure instanceof Error ? failure.message : "配置读取失败，请重试",
        );
    } finally {
      if (!controller.signal.aborted) {
        operation.current = null;
        setBusy("");
      }
    }
  }

  useEffect(() => {
    void load();
    return () => {
      operation.current?.abort();
      operation.current = null;
    };
  }, []);

  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const navigate = (event: Event) => {
      if (dirty && !window.confirm("模型配置有未保存的修改，仍然离开吗？"))
        event.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("retailpilot:before-navigate", navigate);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("retailpilot:before-navigate", navigate);
    };
  }, [dirty]);

  function change(patch: Partial<Draft>) {
    setDraft((previous) => previous && { ...previous, ...patch });
    setTestResult(null);
    setSavedMessage("");
    if (!conflict) setError("");
  }

  async function submit(action: "save" | "test") {
    if (operation.current || !draft || !config || conflict) return;
    const maxOutput = Number(draft.max_output),
      dailyBudget = Number(draft.daily_budget);
    let validation = "";
    if (!Number.isSafeInteger(maxOutput) || maxOutput < 100 || maxOutput > 2000)
      validation = "单次输出上限须为 100–2000 的整数";
    else if (
      !Number.isSafeInteger(dailyBudget) ||
      dailyBudget <= 0 ||
      dailyBudget > 1000000000
    )
      validation = "每日调用预算须为 1–1000000000 的整数";
    else if (draft.provider === "openai-compatible") {
      if (!draft.base_url.trim() || !draft.model_name.trim())
        validation = "请填写服务地址和模型名称";
      else if (
        !draft.api_key.trim() &&
        (!config.has_api_key || draft.clear_api_key)
      )
        validation = "启用大模型协作需要 API Key";
      else if (changedDestination && !draft.api_key.trim())
        validation = "更换服务地址时，请填写新 API Key 后再测试或保存";
    }
    if (validation) {
      setError(validation);
      return;
    }
    const controller = new AbortController();
    operation.current = controller;
    setBusy(action);
    setError("");
    setSavedMessage("");
    setTestResult(null);
    const body = {
      provider: draft.provider,
      base_url: draft.base_url.trim(),
      model_name: draft.model_name.trim(),
      max_output: maxOutput,
      daily_budget: dailyBudget,
      clear_api_key: draft.clear_api_key,
      revision: config.revision,
      ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
    };
    try {
      if (action === "test") {
        const result = await requestModel<ConnectionResult>(
          "/model-config/test",
          controller.signal,
          body,
        );
        if (!controller.signal.aborted) setTestResult(result);
      } else {
        const next = await requestModel<ModelConfig>(
          "/model-config",
          controller.signal,
          body,
        );
        if (controller.signal.aborted) return;
        setConfig(next);
        setDraft(fromConfig(next));
        onChanged(next);
        const message = next.enabled
          ? "已保存，大模型协作已启用"
          : "已保存，当前使用规则模式";
        setSavedMessage(message);
        notify(message);
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (failure instanceof ModelRequestError && failure.status === 409) {
          setConflict(true);
          setError(
            "配置已被其他管理员修改，当前草稿已保留。请重新加载最新配置后再编辑。",
          );
        } else
          setError(
            failure instanceof Error ? failure.message : "请求失败，请稍后重试",
          );
      }
    } finally {
      if (!controller.signal.aborted) {
        operation.current = null;
        setBusy("");
      }
    }
  }

  return (
    <section className="panel model-panel" aria-labelledby="model-config-title">
      <div className="panel-heading">
        <div className="model-heading">
          <Settings2 size={19} />
          <h2 id="model-config-title">模型配置</h2>
        </div>
        <span className="subtle-tag">管理员 · 全工作空间</span>
      </div>
      <div className="model-body">
        <p className="model-intro">
          选择工作空间的分析模式。保存后立即用于新发起的分析，无需重启。
        </p>
        {!draft || !config ? (
          <div className="model-load-state">
            {busy === "load" ? (
              <p role="status">
                <Loader2 size={16} className="spin" /> 正在读取模型配置…
              </p>
            ) : (
              <>
                <p className="error-msg" role="alert">
                  {error}
                </p>
                <button className="btn" onClick={() => void load()}>
                  重新读取配置
                </button>
              </>
            )}
          </div>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit("save");
            }}
          >
            <div className="model-config-meta">
              <span>
                {
                  {
                    database: "网页配置",
                    environment: "环境配置",
                    default: "默认配置",
                  }[config.source]
                }
              </span>
              {config.updated_at && (
                <span>最近保存：{when(config.updated_at)}</span>
              )}
              <span className={dirty ? "model-unsaved" : "muted"}>
                {dirty ? "有未保存的修改" : "与当前配置一致"}
              </span>
            </div>
            <fieldset className="model-fields" disabled={!!busy}>
              <div className="form-grid">
                <label>
                  运行模式
                  <select
                    aria-label="运行模式"
                    value={draft.provider}
                    onChange={(event) =>
                      change({
                        provider: event.target.value as Draft["provider"],
                      })
                    }
                  >
                    <option value="rules">规则模式</option>
                    <option value="openai-compatible">大模型协作</option>
                  </select>
                  <small>
                    大模型服务需兼容 Chat Completions，并支持严格 JSON Schema
                    输出。
                  </small>
                </label>
                <div className="model-mode-note">
                  <SparklesMode
                    enabled={draft.provider === "openai-compatible"}
                  />
                </div>
                <label>
                  服务地址
                  <input
                    aria-label="服务地址"
                    type="url"
                    placeholder="https://api.example.com/v1"
                    value={draft.base_url}
                    required={draft.provider === "openai-compatible"}
                    onChange={(event) =>
                      change({ base_url: event.target.value })
                    }
                  />
                  <small>
                    填写服务基础地址（通常含 /v1），使用 HTTPS；本机服务可使用
                    HTTP。
                  </small>
                </label>
                <label>
                  模型名称
                  <input
                    aria-label="模型名称"
                    placeholder="填写服务商提供的模型名称"
                    value={draft.model_name}
                    required={draft.provider === "openai-compatible"}
                    onChange={(event) =>
                      change({ model_name: event.target.value })
                    }
                  />
                  <small>名称需与服务商提供的模型标识一致。</small>
                </label>
                <label className="model-key-field">
                  API Key
                  <input
                    aria-label="API Key"
                    type="password"
                    autoComplete="new-password"
                    spellCheck={false}
                    value={draft.api_key}
                    placeholder={
                      config.has_api_key
                        ? "留空保留已保存密钥"
                        : "输入模型服务的 API Key"
                    }
                    onChange={(event) =>
                      change({
                        api_key: event.target.value,
                        clear_api_key: false,
                      })
                    }
                    aria-describedby="model-key-hint"
                  />
                  <small id="model-key-hint">
                    {config.has_api_key
                      ? "已保存密钥。出于安全考虑不回显；留空即可保留，填写新值可替换。"
                      : "尚未保存密钥。启用大模型协作时需要填写。"}
                  </small>
                </label>
              </div>
              <label className="checkbox-label model-clear-key">
                <input
                  type="checkbox"
                  checked={draft.clear_api_key}
                  disabled={!config.has_api_key}
                  onChange={(event) =>
                    change({
                      clear_api_key: event.target.checked,
                      ...(event.target.checked ? { api_key: "" } : {}),
                    })
                  }
                />
                清除已保存密钥
              </label>
              {draft.clear_api_key && (
                <p className="model-field-note">
                  保存后将移除密钥。请选择规则模式，或取消清除并填写新密钥。
                </p>
              )}
              {changedDestination && (
                <p className="model-field-note">
                  更换服务地址时必须填写新 API
                  Key，避免将已保存密钥发送到另一服务。
                </p>
              )}
              <details className="model-advanced">
                <summary>高级设置</summary>
                <div className="form-grid">
                  <label>
                    单次输出上限
                    <input
                      aria-label="单次输出上限"
                      type="number"
                      min={100}
                      max={2000}
                      step={1}
                      required
                      value={draft.max_output}
                      onChange={(event) =>
                        change({ max_output: event.target.value })
                      }
                    />
                    <small>100–2000，限制每次模型响应的输出量。</small>
                  </label>
                  <label>
                    每日调用预算
                    <input
                      aria-label="每日调用预算"
                      type="number"
                      min={1}
                      max={1000000000}
                      step={1}
                      required
                      value={draft.daily_budget}
                      onChange={(event) =>
                        change({ daily_budget: event.target.value })
                      }
                    />
                    <small>
                      正整数，按输入大小和输出上限预留额度；达到上限后当日停止模型调用。
                    </small>
                  </label>
                </div>
              </details>
            </fieldset>
            <div className="model-data-note">
              <ShieldMessage />
            </div>
            {error && (
              <div className="notice danger model-feedback" role="alert">
                <AlertCircle size={17} />
                <div>
                  <p>{error}</p>
                  {conflict && (
                    <>
                      <p>重新加载会替换当前未保存的草稿。</p>
                      <button
                        type="button"
                        className="btn"
                        disabled={!!busy}
                        onClick={() => void load()}
                      >
                        重新加载最新配置
                      </button>
                    </>
                  )}
                </div>
              </div>
            )}
            {testResult && (
              <div
                className={
                  "notice model-feedback" + (testResult.ok ? "" : " danger")
                }
                role="status"
              >
                <CheckCircle2 size={17} />
                <div>
                  <p>
                    {testResult.ok
                      ? "测试成功，当前草稿可连接。保存后才会生效。"
                      : "连接测试未通过"}
                  </p>
                  <p>{testResult.message}</p>
                </div>
              </div>
            )}
            {savedMessage && (
              <div className="notice model-feedback" role="status">
                <CheckCircle2 size={17} />
                <p>
                  {savedMessage}。
                  {config.has_api_key
                    ? "密钥不会在网页中回显。"
                    : "未保存密钥。"}
                </p>
              </div>
            )}
            <div className="model-actions">
              <p>测试连接会发送简短校验请求，不会保存配置。</p>
              <div className="button-row">
                <button
                  type="button"
                  className="btn"
                  disabled={!!busy || conflict || draft.provider === "rules"}
                  onClick={() => void submit("test")}
                >
                  {busy === "test" ? (
                    <Loader2 className="spin" size={16} />
                  ) : (
                    <PlugZap size={16} />
                  )}
                  {busy === "test" ? "正在测试…" : "测试连接"}
                </button>
                <button
                  type="submit"
                  className="btn primary"
                  disabled={!!busy || conflict || !dirty}
                >
                  {busy === "save" ? (
                    <Loader2 className="spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  {busy === "save" ? "正在保存…" : "保存配置"}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}

function SparklesMode({ enabled }: { enabled: boolean }) {
  return (
    <p>
      {enabled
        ? "大模型复核分析计划、选择基于证据的后续建议。问题解析与指标计算仍由规则工作流完成。"
        : "按确定性规则完成分析，无需模型服务或 API Key。"}
    </p>
  );
}

function ShieldMessage() {
  return (
    <p>
      启用大模型后，问题、分析计划、授权范围及汇总指标将发送至配置的模型服务，不发送原始交易明细。此配置由管理员统一管理，适用于整个工作空间。
    </p>
  );
}
