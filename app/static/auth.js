// 全站共享的登录态模块（S2-02-2）。挂在 window.CodeMaxAuth 上，
// 子页面（drawio / shop）直接调用。
//
// ## 为什么是外部文件而不是内联脚本
//
// base.html 是所有页面的父模板，**包括 OAuth 同意页** —— 那是发放授权码的
// 安全关键页面，tests/test_oauth_consent.py 明确断言它不含任何内联脚本
// （CSP 虽然因为 TD-163 仍允许 'unsafe-inline'，但新代码不该再添一个理由）。
// 抽成外部文件后由 CSP 的 `script-src 'self'` 覆盖，连 unsafe-inline 都不需要。
//
// ## 为什么「是否已登录」必须问后端
//
// 登录态在 HttpOnly cookie 里，脚本读不到（TD-44）。所以唯一可靠的判断方式
// 是 GET /auth/me，不能靠 localStorage 之类自己记（那正是 TD-44 要消灭的做法）。
// 全站共用的登录态模块。挂在 window 上，子页面（drawio / shop）直接用。
//
// 为什么「是否已登录」必须问后端：登录态在 HttpOnly cookie 里，脚本读不到（TD-44）。
// 所以唯一可靠的判断方式是 GET /auth/me。
window.CodeMaxAuth = (function () {
  const mask = document.getElementById("auth-mask");
  const who = document.getElementById("auth-who");
  const btnAuth = document.getElementById("btn-auth");
  const btnLogout = document.getElementById("btn-logout");
  const err = document.getElementById("auth-error");
  const hint = document.getElementById("auth-hint");
  const title = document.getElementById("auth-title");
  const submit = document.getElementById("auth-submit");
  let mode = "login";
  let user = null;
  const listeners = [];

  function paint() {
    const on = !!user;
    who.hidden = !on;
    btnAuth.hidden = on;
    btnLogout.hidden = !on;
    if (on) who.textContent = user.nickname || user.username;
  }

  function notify() { listeners.forEach((f) => f(user)); }

  function setMode(m) {
    mode = m;
    title.textContent = m === "login" ? "登录" : "注册新账号";
    submit.textContent = m === "login" ? "登录" : "注册";
    hint.hidden = m !== "register";
    document.getElementById("auth-pass").autocomplete =
      m === "login" ? "current-password" : "new-password";
    err.textContent = "";
  }

  function open(m) {
    setMode(m || "login");
    mask.classList.add("open");
    document.getElementById("auth-user").focus();
  }
  function close() { mask.classList.remove("open"); }

  document.getElementById("tab-login").onclick = () => setMode("login");
  document.getElementById("tab-register").onclick = () => setMode("register");
  btnAuth.onclick = () => open("login");
  document.getElementById("auth-cancel").onclick = close;
  mask.onclick = (e) => { if (e.target === mask) close(); };

  btnLogout.onclick = async () => {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    user = null;
    paint();
    notify();
  };

  document.getElementById("auth-form").onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = "";
    const u = document.getElementById("auth-user").value;
    const p = document.getElementById("auth-pass").value;
    try {
      if (mode === "register") {
        // 注册收 JSON（RegisterIn），登录收 form-urlencoded（OAuth2PasswordRequestForm）——
        // 两个接口的入参格式不一样，别搞混。
        const r = await fetch("/auth/register", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: u, password: p }),
        });
        if (!r.ok) {
          err.textContent = (await r.json().catch(() => null))?.detail || `注册失败（${r.status}）`;
          return;
        }
        // ⚠️ /auth/register **不写 cookie**（它只返回 UserOut），所以注册完必须再登录一次。
      }
      const res = await fetch("/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ username: u, password: p }),
      });
      if (!res.ok) {
        err.textContent = (await res.json().catch(() => null))?.detail || `登录失败（${res.status}）`;
        return;
      }
      await refresh();
      close();
    } catch (ex) {
      err.textContent = `网络错误：${ex}`;
    }
  };

  async function refresh() {
    const res = await fetch("/auth/me", { credentials: "same-origin" });
    user = res.ok ? await res.json() : null;
    paint();
    notify();
    return user;
  }

  refresh(); // 进页面就问一次
  return { open, close, refresh, onChange: (f) => listeners.push(f), get user() { return user; } };
})();
