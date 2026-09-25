// 全站共享的登录态模块（S2-02-2）。挂在 window.CodeMaxAuth 上，
// 子页面（drawio / shop）直接调用。
//
// ## 为什么是外部文件而不是内联脚本
//
// base.html 是所有页面的父模板，**包括 OAuth 同意页** —— 那是发放授权码的
// 安全关键页面，共享登录交互不在该模板中新增内联业务脚本。
// 页面 CSP 的具体例外以 app/middleware.py 为准，不能从历史注释推断当前放行范围。
// 抽成外部文件后由 CSP 的 `script-src 'self'` 覆盖，连 unsafe-inline 都不需要。
//
// ## 为什么「是否已登录」必须问后端
//
// 登录态在 HttpOnly cookie 里，脚本读不到（TD-44）。所以唯一可靠的判断方式
// 是 GET /auth/me，不能靠 localStorage 之类自己记（那正是 TD-44 要消灭的做法）。
window.CodeMaxAuth = (function () {
  const mask = document.getElementById("auth-mask");
  const who = document.getElementById("auth-who");
  const btnAuth = document.getElementById("btn-auth");
  const btnLogout = document.getElementById("btn-logout");
  const err = document.getElementById("auth-error");
  const hint = document.getElementById("auth-hint");
  const title = document.getElementById("auth-title");
  const submit = document.getElementById("auth-submit");
  const pwMask = document.getElementById("pw-mask");
  const pwErr = document.getElementById("pw-error");
  const pwDone = document.getElementById("pw-done");
  const pwSubmit = document.getElementById("pw-submit");
  const pwCancel = document.getElementById("pw-cancel");
  let mode = "login";
  let user = null;
  let authSeq = 0;
  // 是否已经问过服务端并拿到过答案（成功或失败都算）。启动瞬间 user 是 null，
  // 但那是「还没问完」而不是「访客」—— 页面据此避免把「登录态稍后到达」误当成「换账号」。
  let settled = false;

  function errorText(data, status) {
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((x) => x.msg || "输入无效").join("；");
    return `请求失败（${status}）`;
  }
  const listeners = [];

  function paint() {
    const on = !!user;
    who.hidden = !on;
    btnAuth.hidden = on;
    btnLogout.hidden = !on;
    if (on) {
      const name = user.nickname || user.username;
      who.textContent = name;
      // 按钮的可见文字只有用户名；读屏需要知道点它会做什么（可见文字仍包含在名称里，WCAG 2.5.3）
      who.ariaLabel = `${name}（修改密码）`;
    } else {
      // 退出或会话到期时，改密浮层不能还开着（它属于刚刚离开的那个账号）
      pwMask.classList.remove("open");
    }
    // 顶栏的「订单管理（管理员）」对访客与普通用户都是死链接（点进去只看到「请使用管理员账号登录」），
    // 所以默认 hidden，只有确认管理员身份才显示；页脚另有一个常驻入口，管理员登录前也找得到。
    // 这只是导航整洁，**不是权限**：真正的判断在 require_admin（读库、不看前端控件）。
    const adminEntry = document.getElementById("admin-entry");
    if (adminEntry) adminEntry.hidden = !(on && user.role === 1);
  }

  // 遍历**副本**：监听器可能在回调里退订自己（shop 页的「登录后补一次下单」就是这样），
  // 直接 forEach 原数组会因 splice 导致后续元素被跳过 —— 那类 bug 只在有多个监听器时出现。
  //
  // reason 是给订阅页的第二个参数（默认 "sync"，不影响只用一个参数的旧回调）：
  //   "sync"    —— 常规登录态同步：退出、登录、换账号、首次加载
  //   "expired" —— 会话在页面打开期间到期（401）。这一种**必须与主动退出区分开**：
  //                主动退出表示「我不想再看了」，页面清空是合理的；到期只是凭证过期，
  //                用户画了一半的图、写了一半的留言还在，绝不能在提示「请重新登录」的同时把它丢掉。
  function notify(reason = "sync") { listeners.slice().forEach((f) => f(user, reason)); }

  function setMode(m) {
    mode = m;
    title.textContent = m === "login" ? "登录" : "注册新账号";
    submit.textContent = m === "login" ? "登录" : "注册";
    hint.hidden = m !== "register";
    document.getElementById("auth-pass").autocomplete =
      m === "login" ? "current-password" : "new-password";
    err.textContent = "";
  }

  // 浮层可访问性（TD-263）：打开时记住触发元素，关闭后把焦点还回去；Esc 关闭。
  // 浮层的 role="dialog" / aria-modal / aria-labelledby 写在 base.html 上。
  let opener = null;
  function open(m) {
    setMode(m || "login");
    opener = document.activeElement || null;
    mask.classList.add("open");
    document.getElementById("auth-user").focus();
  }
  // 只在焦点还留在浮层里时归还：用户已经点到别处就不要抢回来
  function returnFocus(box, from) {
    const active = document.activeElement;
    if (from && typeof from.focus === "function" && (!active || active === document.body || box.contains?.(active))) {
      from.focus();
    }
  }
  function close() {
    mask.classList.remove("open");
    returnFocus(mask, opener);
    opener = null;
  }

  document.getElementById("tab-login").onclick = () => setMode("login");
  document.getElementById("tab-register").onclick = () => setMode("register");
  btnAuth.onclick = () => open("login");
  document.getElementById("auth-cancel").onclick = close;
  mask.onclick = (e) => { if (e.target === mask) close(); };
  mask.onkeydown = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };

  // ---- 修改密码（V-06）：与登录浮层同样的焦点归还与 Esc 关闭。三个密码框在打开和关闭时都清空，
  // 理由同登录成功后清密码框：浮层只是隐藏，DOM 还在。
  let pwOpener = null;
  const pwFields = ["pw-old", "pw-new", "pw-again"];
  function clearPasswordFields() { pwFields.forEach((id) => { document.getElementById(id).value = ""; }); }
  function openPassword() {
    if (!user) return open("login");
    clearPasswordFields();
    document.getElementById("pw-user").value = user.username;
    document.getElementById("pw-account").textContent = `当前账号：${user.username}`;
    pwErr.textContent = ""; pwDone.textContent = ""; pwCancel.textContent = "取消";
    pwOpener = document.activeElement || null;
    pwMask.classList.add("open");
    document.getElementById("pw-old").focus();
  }
  function closePassword() {
    pwMask.classList.remove("open");
    clearPasswordFields();
    returnFocus(pwMask, pwOpener);
    pwOpener = null;
  }
  who.onclick = openPassword;
  pwCancel.onclick = closePassword;
  pwMask.onclick = (e) => { if (e.target === pwMask) closePassword(); };
  pwMask.onkeydown = (e) => { if (e.key === "Escape") { e.stopPropagation(); closePassword(); } };

  document.getElementById("pw-form").onsubmit = async (e) => {
    e.preventDefault();
    if (pwSubmit.disabled) return;
    const oldPass = document.getElementById("pw-old").value;
    const newPass = document.getElementById("pw-new").value;
    pwErr.textContent = ""; pwDone.textContent = "";
    // 这两条服务端也会挡（相同密码 400）；前端先挡是为了不白白消耗改密限流额度
    if (newPass !== document.getElementById("pw-again").value) { pwErr.textContent = "两次输入的新密码不一致"; return; }
    if (newPass === oldPass) { pwErr.textContent = "新密码不能与原密码相同"; return; }
    pwSubmit.disabled = true;
    try {
      const res = await fetch("/auth/password", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_password: oldPass, new_password: newPass }),
      });
      // 原密码错是 400；401 只可能是会话本身已到期 —— 交给统一的到期处理（清用户、弹登录浮层）
      if (res.status === 401) { closePassword(); sessionExpired(401); return; }
      if (!res.ok) { pwErr.textContent = errorText(await res.json().catch(() => null), res.status); return; }
      // 服务端已经把本会话的 cookie 换成新 token，用户快照不变，不需要 refresh()
      clearPasswordFields();
      pwDone.textContent = "密码已修改。其他设备和浏览器上的登录已失效，需要用新密码重新登录；本页保持登录。";
      pwCancel.textContent = "关闭";
      pwCancel.focus();
    } catch (ex) {
      pwErr.textContent = `网络错误：${ex}`;
    } finally { pwSubmit.disabled = false; }
  };

  btnLogout.onclick = async () => {
    const stamp = ++authSeq;
    try {
      const res = await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
      if (!res.ok) throw new Error(`退出失败（${res.status}）`);
      if (stamp !== authSeq) return;
      user = null; paint(); notify();
    } catch (e) { if (stamp === authSeq) { open("login"); err.textContent = e.message; } }
  };

  document.getElementById("auth-form").onsubmit = async (e) => {
    e.preventDefault();
    if (submit.disabled) return;
    ++authSeq; submit.disabled = true;
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
          err.textContent = errorText(await r.json().catch(() => null), r.status);
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
        err.textContent = errorText(await res.json().catch(() => null), res.status);
        return;
      }
      // 登录成功就清掉密码框：浮层只是被隐藏、DOM 仍在，否则明文密码会一直留在页面里，
      // 下次（例如退出后）打开浮层还原样填着上一个人的密码。用户名保留，方便同一账号重登。
      document.getElementById("auth-pass").value = "";
      await refresh();
      close();
    } catch (ex) {
      err.textContent = `网络错误：${ex}`;
    } finally { submit.disabled = false; }
  };

  async function refresh() {
    const stamp = ++authSeq;
    try {
      const res = await fetch("/auth/me", { credentials: "same-origin" });
      const next = res.ok ? await res.json() : null;
      if (next !== null && (typeof next !== "object" || !next.username)) throw new Error("登录响应格式无效");
      if (stamp !== authSeq) return user;
      user = next; settled = true; paint(); notify(); return user;
    } catch (e) {
      settled = true;
      if (stamp === authSeq) { user = null; paint(); notify(); }
      throw e;
    }
  }

  refresh().catch(() => { err.textContent = "暂时无法确认登录状态，请检查网络后重试"; });

  // 会话在页面打开期间到期（ACCESS_TOKEN_EXPIRE_MINUTES，默认 30 分钟）时，页面脚本收到的只是一个 401：
  // 顶栏仍显示"已登录"，保存/发送只报"未登录"。各页把 401 交给这里：清掉过期的用户快照、通知监听器、
  // 弹出登录浮层并给出原因（TD-270）。返回值告诉调用方"已处理"，调用方不必再显示自己的错误。
  function sessionExpired(status) {
    if (status !== 401 || !user) return false;
    user = null; paint(); notify("expired");
    open("login");
    err.textContent = "登录已过期，请重新登录后继续；刚才的操作未提交。";
    return true;
  }
  return {
    errorText,
    open,
    close,
    openPassword,
    refresh,
    sessionExpired,
    // **返回退订函数**。早先只 push、没有退订接口，于是 shop 页那个
    // 「登录成功后自动补一次下单」的监听器永久残留：用户下次登录（哪怕没点购买）
    // 会再触发一次 buy()；未登录时多点几次「立即购买」还会累积多个监听器，
    // 一次登录就触发多次下单。实测复现过：只重新登录一次，下单调用 2 → 3。
    // 返回函数而不是提供 off(f)，是因为调用方不必自己保存 f 的引用，也不会退订错人。
    onChange: (f) => {
      listeners.push(f);
      return () => {
        const i = listeners.indexOf(f);
        if (i >= 0) listeners.splice(i, 1);
      };
    },
    get user() {
      return user;
    },
    // 供页面区分「启动中」与「确实是访客」：只有 === false 才表示首次 /auth/me 还没回来。
    get settled() {
      return settled;
    },
  };
})();
