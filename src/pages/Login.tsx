import React, { useState } from "react";
import { ArrowRight, BarChart3, Loader2 } from "lucide-react";
import { Obj, api } from "../lib";

export default function Login({
  onLogin,
  notice,
}: {
  onLogin: (u: Obj) => void;
  notice?: string;
}) {
  const [name, setName] = useState(""),
    [registering, setRegistering] = useState(false),
    [displayName, setDisplayName] = useState(""),
    [password, setPassword] = useState(""),
    [confirmPassword, setConfirmPassword] = useState(""),
    [success, setSuccess] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className={"login-page" + (registering ? " registering" : "")}>
      <section className="login-story">
        <a className="brand">
          <span className="brand-symbol">
            <BarChart3 />
          </span>
          <span>
            RetailPilot<small>零售智航</small>
          </span>
        </a>
        <div className="login-copy">
          <span className="eyebrow">YOUR DATA. YOUR NEXT MOVE.</span>
          <h1>
            让每一份数据，
            <br />
            都有经营的方向。
          </h1>
          <p>
            从经营变化到可信洞察，
            <br />
            让数据支持你的每一次决策。
          </p>
          <span className="story-foot">
            数据准备 <ArrowRight size={15} /> 智能分析 <ArrowRight size={15} />{" "}
            决策报告
          </span>
        </div>
        <small>RETAIL INTELLIGENCE, WITH EVIDENCE.</small>
      </section>
      <section className="login-form">
        <div className="login-box">
          <span className="login-mark">
            <BarChart3 size={28} />
          </span>
          <h2>{registering ? "创建你的账号" : "欢迎回到工作空间"}</h2>
          <p>
            {registering
              ? "注册后即可登录，经营数据由管理员授权。"
              : "登录零售智航，开启今天的经营洞察。"}
          </p>
          {notice && !registering && !success && (
            <div className="error-msg" role="alert">
              {notice}
            </div>
          )}
          {success && (
            <div className="login-feedback" role="status">
              {success}
            </div>
          )}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy) return;
              if (registering && password !== confirmPassword) {
                setError("两次密码不一致，请重新确认。");
                return;
              }
              setBusy(true);
              setError("");
              try {
                if (registering) {
                  await api("/register", {
                    username: name.trim(),
                    name: displayName.trim(),
                    password,
                    confirm_password: confirmPassword,
                  });
                  setName(name.trim());
                  setPassword("");
                  setConfirmPassword("");
                  setRegistering(false);
                  setSuccess(
                    "注册成功，请使用新账号登录。查看经营数据前，请联系管理员授权门店或调整角色。",
                  );
                } else {
                  onLogin(await api("/login", { username: name, password }));
                }
              } catch (e: any) {
                setError(e.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              账号
              <input
                autoComplete="username"
                aria-label="账号"
                required
                disabled={busy}
                minLength={registering ? 3 : undefined}
                maxLength={40}
                pattern={registering ? "[A-Za-z0-9_.\\-]{3,40}" : undefined}
                aria-describedby={
                  registering ? "register-account-help" : undefined
                }
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              {registering && (
                <small id="register-account-help" className="login-hint">
                  3–40 位字母、数字、下划线、点或连字符
                </small>
              )}
            </label>
            {registering && (
              <label>
                姓名
                <input
                  autoComplete="name"
                  required
                  disabled={busy}
                  maxLength={80}
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </label>
            )}
            <label>
              密码
              <input
                type="password"
                aria-label="密码"
                autoComplete={registering ? "new-password" : "current-password"}
                required
                disabled={busy}
                minLength={registering ? 12 : undefined}
                maxLength={1024}
                aria-describedby={
                  registering ? "register-password-help" : undefined
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {registering && (
                <small id="register-password-help" className="login-hint">
                  密码至少 12 位
                </small>
              )}
            </label>
            {registering && (
              <label>
                确认密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  disabled={busy}
                  maxLength={1024}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </label>
            )}
            {error && (
              <div className="error-msg" role="alert">
                {error}
              </div>
            )}
            <button className="btn primary" disabled={busy}>
              {busy ? (
                <Loader2 className="spin" size={17} />
              ) : (
                <>
                  {registering ? "注册账号" : "进入工作空间"}{" "}
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>
          <div className="login-switch">
            {registering ? "已有账号？" : "还没有账号？"}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setRegistering(!registering);
                setPassword("");
                setConfirmPassword("");
                setError("");
                setSuccess("");
              }}
            >
              {registering ? "返回登录" : "注册新账号"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
