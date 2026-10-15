import React, { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, LogOut, ShieldCheck, Sparkles, Users } from "lucide-react";
import { Obj, api, User } from "../lib";
import { Modal } from "../components/ui";
import ModelConfiguration, {
  ModelConfig,
} from "../components/ModelConfiguration";

export default function Settings({
  user,
  notify,
  onUserUpdated,
  logout,
}: {
  user: User;
  notify: (s: string) => void;
  onUserUpdated: () => Promise<void>;
  logout: () => void;
}) {
  const [users, setUsers] = useState<Obj[]>([]),
    [edit, setEdit] = useState<Obj | null>(null),
    [stores, setStores] = useState(""),
    [currentPassword, setCurrentPassword] = useState(""),
    [newPassword, setNewPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [catalog, setCatalog] = useState<Obj[]>([]),
    [resetPassword, setResetPassword] = useState("");
  const [modelEnabled, setModelEnabled] = useState<boolean | null>(null);
  const [modelStatusError, setModelStatusError] = useState("");
  const [modelStatusLoading, setModelStatusLoading] = useState(true);
  const statusRequest = useRef<AbortController | null>(null);
  async function loadModelStatus() {
    statusRequest.current?.abort();
    const controller = new AbortController();
    statusRequest.current = controller;
    setModelStatusLoading(true);
    setModelStatusError("");
    try {
      const status = await api("/model-status", undefined, controller.signal);
      if (!controller.signal.aborted) setModelEnabled(!!status.enabled);
    } catch (failure) {
      if (!controller.signal.aborted)
        setModelStatusError(
          failure instanceof Error ? failure.message : "运行模式读取失败",
        );
    } finally {
      if (!controller.signal.aborted) setModelStatusLoading(false);
    }
  }
  const onModelChanged = useCallback((config: ModelConfig) => {
    statusRequest.current?.abort();
    setModelEnabled(config.enabled);
    setModelStatusError("");
    setModelStatusLoading(false);
  }, []);
  useEffect(() => {
    api("/stores")
      .then(setCatalog)
      .catch((e) => notify(e.message));
    if (user.roles.includes("admin"))
      api("/users")
        .then(setUsers)
        .catch((e) => notify(e.message));
    void loadModelStatus();
    return () => statusRequest.current?.abort();
  }, []);
  return (
    <>
      <section className="panel settings-account">
        <span className="avatar large">{user.name[0]}</span>
        <div>
          <h2>{user.name}</h2>
          <p>
            账号：{user.id} · 角色：
            {user.roles
              .map(
                (r: string) =>
                  (
                    ({
                      analyst: "数据分析",
                      manager: "经营管理",
                      admin: "系统管理",
                      store: "门店人员",
                    }) as Record<string, string>
                  )[r],
              )
              .join(" / ")}
          </p>
          <p>授权门店：{user.stores.join("、") || "无经营数据授权"}</p>
        </div>
        <button className="btn" onClick={logout}>
          <LogOut size={16} />
          退出登录
        </button>
      </section>
      <section className="panel password-panel">
        <div>
          <h2>修改密码</h2>
          <p>新密码至少 12 位。修改后，其他设备上的登录会失效。</p>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              await api("/me/password", {
                current_password: currentPassword,
                new_password: newPassword,
              });
              setCurrentPassword("");
              setNewPassword("");
              notify("密码已更新");
            } catch (error: any) {
              notify(error.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            当前密码
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </label>
          <label>
            新密码
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </label>
          <button
            className="btn"
            disabled={busy || !currentPassword || newPassword.length < 12}
          >
            更新密码
          </button>
        </form>
      </section>
      {user.roles.includes("admin") && (
        <ModelConfiguration notify={notify} onChanged={onModelChanged} />
      )}
      <div className="settings-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>分析运行模式</h2>
            <span className="subtle-tag">
              {modelStatusLoading
                ? "读取中"
                : modelStatusError
                  ? "待重试"
                  : modelEnabled
                    ? "大模型协作"
                    : "规则模式"}
            </span>
          </div>
          <div className="settings-body">
            <span className="settings-icon">
              <Sparkles size={24} />
            </span>
            {modelStatusLoading ? (
              <p className="model-status-loading" role="status">
                <Loader2 className="spin" size={16} /> 正在读取运行模式…
              </p>
            ) : modelStatusError ? (
              <div className="model-status-error" role="alert">
                <h3>暂时无法读取运行模式</h3>
                <p>{modelStatusError}</p>
                <button
                  className="text-btn"
                  onClick={() => void loadModelStatus()}
                >
                  重新读取运行模式
                </button>
              </div>
            ) : (
              <>
                <h3>
                  {modelEnabled ? "大模型协作已启用" : "确定性规则工作流"}
                </h3>
                <p className="model-runtime-description">
                  {modelEnabled
                    ? "大模型复核分析计划、选择基于证据的后续建议。问题解析、权限校验与指标计算仍由规则工作流完成。"
                    : "按确定性规则完成问题解析、规划、查询、统计、结果核验和报告编制。当前未启用外部大模型。"}
                </p>
                {!user.roles.includes("admin") && (
                  <p className="model-admin-hint">
                    模型配置由工作空间管理员统一管理。
                  </p>
                )}
              </>
            )}
            <dl>
              <div>
                <dt>指标版本</dt>
                <dd>sales-v1</dd>
              </div>
              <div>
                <dt>计算方式</dt>
                <dd>整数分 · 精确聚合</dd>
              </div>
              <div>
                <dt>数据存储</dt>
                <dd>本地 SQLite</dd>
              </div>
              <div>
                <dt>可同时运行任务</dt>
                <dd>2 个</dd>
              </div>
            </dl>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>数据与权限原则</h2>
            <ShieldCheck size={19} />
          </div>
          <div className="settings-body">
            <div className="definition-list">
              {[
                [
                  "角色与范围双重校验",
                  "角色决定可执行操作，门店授权决定可见数据。",
                ],
                [
                  "历史记录重新校验",
                  "任务、报告和导出在每次访问时检查当前权限。",
                ],
                [
                  "管理员不自动读取经营数据",
                  "管理员账号默认没有门店数据访问权限。",
                ],
                [
                  "数据来源可追溯",
                  "每个发布版本记录上传时填写的来源与快照时间。",
                ],
              ].map(([t, d]) => (
                <div key={t}>
                  <h3>{t}</h3>
                  <p>{d}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
      {user.roles.includes("admin") && (
        <section className="panel">
          <div className="panel-heading">
            <h2>账号与门店授权</h2>
            <button
              className="btn"
              onClick={() => {
                setEdit({
                  id: "",
                  name: "",
                  password: "",
                  roles: ["analyst"],
                  stores: [],
                  new: true,
                });
                setStores("");
              }}
            >
              <Users size={18} /> 新建账号
            </button>
          </div>
          <table>
            <thead>
              <tr>
                <th>账号</th>
                <th>角色</th>
                <th>门店范围</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    {u.name} · {u.id}
                  </td>
                  <td>{u.roles.join(", ")}</td>
                  <td>{u.stores.join(", ") || "未授权"}</td>
                  <td>
                    <button
                      className="text-btn"
                      onClick={() => {
                        setEdit({ ...u });
                        setStores(u.stores.join(", "));
                      }}
                    >
                      编辑权限
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {edit && (
        <Modal
          title={edit.new ? "新建账号" : `编辑 ${edit.name} 的权限`}
          onClose={() => setEdit(null)}
        >
          {edit.new && (
            <>
              <label>
                登录账号
                <input
                  value={edit.id}
                  onChange={(e) => setEdit({ ...edit, id: e.target.value })}
                />
              </label>
              <label>
                姓名
                <input
                  value={edit.name}
                  onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                />
              </label>
              <label>
                初始密码（至少 12 位）
                <input
                  type="password"
                  autoComplete="new-password"
                  value={edit.password}
                  onChange={(e) =>
                    setEdit({ ...edit, password: e.target.value })
                  }
                />
              </label>
            </>
          )}
          <div className="role-checkboxes">
            {[
              ["admin", "系统管理员"],
              ["analyst", "数据分析人员"],
              ["manager", "经营管理人员"],
              ["store", "门店人员"],
            ].map(([id, label]) => (
              <label className="checkbox-label" key={id}>
                <input
                  type="checkbox"
                  checked={edit.roles.includes(id)}
                  onChange={(e) =>
                    setEdit({
                      ...edit,
                      roles: e.target.checked
                        ? [...edit.roles, id]
                        : edit.roles.filter((r: string) => r !== id),
                    })
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={edit.enabled !== false}
              onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })}
            />
            账号可登录
          </label>
          <div className="role-checkboxes">
            {catalog.map((s) => (
              <label className="checkbox-label" key={s.store_id}>
                <input
                  type="checkbox"
                  checked={stores
                    .split(",")
                    .map((s) => s.trim())
                    .includes(s.store_id)}
                  onChange={(e) => {
                    const ids = stores
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean);
                    setStores(
                      (e.target.checked
                        ? [...new Set([...ids, s.store_id])]
                        : ids.filter((id) => id !== s.store_id)
                      ).join(","),
                    );
                  }}
                />
                {s.name} · {s.store_id}
              </label>
            ))}
          </div>
          <p>
            待导入确认：
            {stores
              .split(",")
              .map((s) => s.trim())
              .filter((s) => s && !catalog.some((x) => x.store_id === s))
              .join("、") || "无"}
          </p>
          {!edit.new && (
            <label>
              重置密码（至少 12 位）
              <input
                type="password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
              />
              <button
                className="btn"
                disabled={busy || resetPassword.length < 12}
                onClick={async () => {
                  try {
                    await api(`/users/${edit.id}/reset-password`, {
                      password: resetPassword,
                    });
                    setResetPassword("");
                    notify("密码已重置，该账号需重新登录");
                  } catch (e: any) {
                    notify(e.message);
                  }
                }}
              >
                重置密码并退出已有会话
              </button>
            </label>
          )}
          <label>
            授权门店 ID（英文逗号分隔）
            <input
              value={stores}
              onChange={(e) => setStores(e.target.value)}
              placeholder="填写实际门店 ID，用英文逗号分隔"
            />
          </label>
          <div className="notice">
            授权变更立即影响历史任务、报告和下载访问。可填写新门店
            ID，供后续数据导入使用。
          </div>
          <div className="modal-actions">
            <button
              className="btn primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api(edit.new ? "/users" : "/users/" + edit.id, {
                    ...(edit.new
                      ? {
                          id: edit.id.trim(),
                          name: edit.name.trim(),
                          password: edit.password,
                        }
                      : {}),
                    enabled: edit.enabled !== false,
                    roles: edit.roles,
                    stores: stores
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  });
                  setUsers(await api("/users"));
                  if (!edit.new && edit.id === user.id) await onUserUpdated();
                  setEdit(null);
                  notify(edit.new ? "账号已创建" : "授权已更新");
                } catch (e: any) {
                  notify(e.message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              保存权限
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
