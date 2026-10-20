/* Страница администратора Noctis (открывается сочетанием Alt+X). Чистый JavaScript.
   Данные сервера выводятся только как текст (textContent). Пароли пользователей не показываются: на сервере хранятся только хэши. */
(function () {
  "use strict";
  var CFG = window.NOCTIS_CONFIG || {};
  var root = document.getElementById("admin");
  if (!root) return;

  function el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) { c.forEach(function (x) { n.appendChild(x); }); continue; }
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function field(label, input) { return el("label", { class: "field" }, el("span", { text: label }), input); }
  function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function ssSet(k, v) { try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) { /* нет хранилища */ } }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function fmt(iso) {
    if (!iso) return "—";
    var d = new Date(typeof iso === "string" && !/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso + "Z" : iso);
    return isNaN(d) ? "—" : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  var devMode = !!CFG.showServerField || lsGet("noctis-dev") === "1";
  var state = { server: (devMode && lsGet("noctis-server")) || (CFG.defaultServer || (/^https?:/.test(location.protocol) ? location.origin : "http://localhost:8000")), tokens: null, me: null, tab: "users", q: "" };
  try { state.tokens = JSON.parse(ssGet("noctis-admin-tokens") || "null"); } catch (e) { state.tokens = null; }

  /* ---------- API ---------- */
  function ApiError(status, detail) { this.status = status; this.detail = detail; }
  function base() { return state.server.replace(/\/+$/, ""); }
  async function raw(method, path, body, auth) {
    var headers = {};
    if (auth && state.tokens) headers.Authorization = "Bearer " + state.tokens.access_token;
    var opts = { method: method, headers: headers };
    if (body) { opts.body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
    var r;
    try { r = await fetch(base() + path, opts); } catch (e) { throw new ApiError(0, ""); }
    if (r.status === 204) return null;
    var data = null;
    try { data = await r.json(); } catch (e) { /* пусто */ }
    if (!r.ok) throw new ApiError(r.status, data && typeof data.detail === "string" ? data.detail : "");
    return data;
  }
  async function api(method, path, body) {
    try { return await raw(method, path, body, true); }
    catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401 || !state.tokens) throw e;
      try {
        var pair = await raw("POST", "/api/v1/auth/refresh", { refresh_token: state.tokens.refresh_token }, false);
        state.tokens = pair; ssSet("noctis-admin-tokens", JSON.stringify(pair));
      } catch (e2) { logout(); throw e; }
      return raw(method, path, body, true);
    }
  }
  function errText(e) {
    if (!(e instanceof ApiError)) return "Ошибка.";
    if (e.status === 0) return "Сервер недоступен.";
    if (e.status === 401) return "Неверная почта или пароль.";
    if (e.status === 403) return e.detail || "Недостаточно прав.";
    if (e.status === 429) return "Слишком много попыток. Подождите минуту.";
    return "Ошибка сервера (" + e.status + ").";
  }

  var msgBox = el("div", { role: "status", "aria-live": "polite" });
  function say(text, kind) { clear(msgBox); if (text) msgBox.appendChild(el("div", { class: "msg " + (kind || "ok"), text: text })); }
  async function run(fn) { try { say(""); return await fn(); } catch (e) { say(errText(e), "err"); return undefined; } }

  /* ---------- вход ---------- */
  function renderLogin() {
    clear(root);
    var email = el("input", { type: "email", required: true, autocomplete: "username" });
    var pass = el("input", { type: "password", required: true, autocomplete: "current-password" });
    var go = el("button", { class: "btn btn--primary", type: "submit", text: "Войти" });
    var form = el("form", { class: "panel auth" }, el("h2", { text: "Администратор" }), msgBox,
      field("Почта", email), field("Пароль", pass), go);
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      go.disabled = true;
      await run(async function () {
        var pair = await raw("POST", "/api/v1/auth/login", { email: email.value.trim(), password: pass.value }, false);
        state.tokens = pair;
        var me = await raw("GET", "/api/v1/me", null, true);
        if (me.role !== "admin") { state.tokens = null; throw new ApiError(403, "Эта учётная запись не администратор."); }
        ssSet("noctis-admin-tokens", JSON.stringify(pair));
        state.me = me;
        renderApp();
      });
      go.disabled = false;
    });
    root.appendChild(form);
    email.focus();
  }
  function logout() {
    if (state.tokens) raw("POST", "/api/v1/auth/logout", { refresh_token: state.tokens.refresh_token }, true).catch(function () { /* уже вышли */ });
    state.tokens = null; state.me = null; ssSet("noctis-admin-tokens", null);
    renderLogin();
  }

  /* ---------- каркас ---------- */
  function renderApp() {
    clear(root);
    var tabs = el("div", { class: "tabs", role: "tablist" });
    [["users", "Пользователи"], ["audit", "Журнал"]].forEach(function (x) {
      tabs.appendChild(el("button", { class: "tab", role: "tab", "aria-selected": state.tab === x[0] ? "true" : "false", text: x[1],
        onclick: function () { state.tab = x[0]; renderApp(); } }));
    });
    root.appendChild(el("div", { class: "bar" }, el("h2", { text: "Администратор: " + (state.me ? state.me.email : "") }),
      el("button", { class: "btn btn--sm", text: "Выйти", onclick: logout })));
    root.appendChild(msgBox);
    var stats = el("div", { class: "stats-row" });
    root.appendChild(stats);
    root.appendChild(tabs);
    var body = el("div", { id: "admin-body" });
    root.appendChild(body);
    run(async function () {
      var s = await api("GET", "/api/v1/admin/stats");
      [["Пользователи", s.users], ["Активные", s.active_users], ["Колонки", s.speakers], ["Онлайн", s.speakers_online],
       ["Таймеры", s.timers], ["Будильники", s.alarms], ["Треки", s.tracks], ["Реплики в истории", s.history_entries]].forEach(function (x) {
        stats.appendChild(el("div", { class: "stat" }, el("b", { text: String(x[1]) }), el("span", { text: x[0] })));
      });
    });
    if (state.tab === "audit") renderAudit(body); else renderUsers(body);
  }

  /* ---------- пользователи ---------- */
  function renderUsers(body) {
    var search = el("input", { type: "search", placeholder: "Поиск по почте или имени", value: state.q, maxlength: "80" });
    var table = el("div", { class: "tbl-wrap" });
    var detail = el("div", { id: "admin-detail" });
    search.addEventListener("input", function () { state.q = search.value; clearTimeout(search._t); search._t = setTimeout(load, 300); });
    body.appendChild(el("div", { class: "panel" }, search));
    body.appendChild(table);
    body.appendChild(detail);

    function load() {
      run(async function () {
        var rows = await api("GET", "/api/v1/admin/users?limit=100&q=" + encodeURIComponent(state.q.trim()));
        clear(table);
        var t = el("table", { class: "tbl" });
        t.appendChild(el("thead", null, el("tr", null, ["Имя", "Почта", "Роль", "Статус", "Колонки", "Регистрация", "Последний вход"].map(function (h) { return el("th", { text: h }); }))));
        var tb = el("tbody");
        rows.forEach(function (u) {
          var tr = el("tr", { class: "clickable", tabindex: "0", onclick: function () { open(u.id); }, onkeydown: function (e) { if (e.key === "Enter") open(u.id); } },
            el("td", { text: u.display_name || "—" }), el("td", { text: u.email }), el("td", { text: u.role === "admin" ? "админ" : "пользователь" }),
            el("td", { text: u.is_active ? "активен" : "отключён" }), el("td", { text: u.online_devices + "/" + u.devices }),
            el("td", { text: fmt(u.created_at) }), el("td", { text: fmt(u.last_login_at) }));
          tb.appendChild(tr);
        });
        if (!rows.length) tb.appendChild(el("tr", null, el("td", { colspan: "7", class: "empty", text: "Никого не найдено." })));
        t.appendChild(tb);
        table.appendChild(t);
      });
    }

    function open(id) {
      run(async function () {
        var u = await api("GET", "/api/v1/admin/users/" + id);
        clear(detail);
        var act = function (label, fn, danger) {
          return el("button", { class: "btn btn--sm" + (danger ? " btn--danger" : ""), text: label, onclick: function () { run(fn); } });
        };
        var isAdmin = u.role === "admin";
        var tempBox = el("div");
        var actions = isAdmin ? el("p", { class: "muted", text: "Учётные записи администраторов здесь не изменяются." }) : el("div", { class: "row" },
          u.is_active ? act("Отключить", async function () { await api("POST", "/api/v1/admin/users/" + id + "/disable"); say("Учётная запись отключена."); open(id); load(); })
                      : act("Включить", async function () { await api("POST", "/api/v1/admin/users/" + id + "/enable"); say("Учётная запись включена."); open(id); load(); }),
          act("Сбросить пароль", async function () {
            if (!confirm("Сбросить пароль и завершить все сессии пользователя?")) return;
            var r = await api("POST", "/api/v1/admin/users/" + id + "/reset-password");
            clear(tempBox);
            tempBox.appendChild(el("div", { class: "msg ok" }, "Временный пароль (показывается один раз): ", el("code", { text: r.temporary_password }),
              el("br"), "Передайте его пользователю: он должен сменить пароль в профиле."));
          }),
          act("Удалить учётную запись", async function () {
            if (!confirm("Удалить учётную запись " + u.email + " и все её данные? Это нельзя отменить.")) return;
            await api("DELETE", "/api/v1/admin/users/" + id); clear(detail); say("Учётная запись удалена."); load();
          }, true));
        var spk = el("div", { class: "tbl-wrap" });
        var st = el("table", { class: "tbl" });
        st.appendChild(el("thead", null, el("tr", null, ["Колонка", "Статус", "Прошивка", "Был в сети", "Язык", "Слово", "Таймеры", "Будильники", ""].map(function (h) { return el("th", { text: h }); }))));
        var sb = el("tbody");
        u.speakers.forEach(function (d) {
          sb.appendChild(el("tr", null, el("td", { text: d.name }), el("td", { text: d.online ? "онлайн" : "не в сети" }), el("td", { text: d.firmware_version || "—" }),
            el("td", { text: fmt(d.last_seen) }), el("td", { text: d.language }), el("td", { text: d.wake_word }), el("td", { text: String(d.timers) }), el("td", { text: String(d.alarms) }),
            el("td", null, act("Удалить", async function () {
              if (!confirm("Удалить колонку «" + d.name + "»?")) return;
              await api("DELETE", "/api/v1/admin/devices/" + encodeURIComponent(d.public_id)); say("Колонка удалена."); open(id); load();
            }, true))));
        });
        if (!u.speakers.length) sb.appendChild(el("tr", null, el("td", { colspan: "9", class: "empty", text: "Колонок нет." })));
        st.appendChild(sb); spk.appendChild(st);
        detail.appendChild(el("div", { class: "panel" },
          el("h3", { text: (u.display_name || "Без имени") + " — " + u.email }),
          el("p", { class: "muted", text: "Язык: " + (u.language || "—") + " · история " + (u.history_enabled ? "включена" : "выключена") + " · собственных треков: " + u.tracks
            + " · согласие на обработку данных: " + fmt(u.consent_at) }),
          el("p", { class: "muted", text: "Пароль хранится только в виде необратимого хэша и не может быть показан; при необходимости сбросьте его." }),
          actions, tempBox, spk));
        detail.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    load();
  }

  /* ---------- журнал ---------- */
  function renderAudit(body) {
    var table = el("div", { class: "tbl-wrap" });
    body.appendChild(table);
    run(async function () {
      var rows = await api("GET", "/api/v1/admin/audit?limit=100");
      var t = el("table", { class: "tbl" });
      t.appendChild(el("thead", null, el("tr", null, ["Время", "Администратор", "Действие", "Объект", "IP"].map(function (h) { return el("th", { text: h }); }))));
      var names = { login: "вход", user_disable: "отключение", user_enable: "включение", user_reset_password: "сброс пароля", user_delete: "удаление пользователя", device_delete: "удаление колонки" };
      var tb = el("tbody");
      rows.forEach(function (r) { tb.appendChild(el("tr", null, el("td", { text: fmt(r.at) }), el("td", { text: r.admin }), el("td", { text: names[r.action] || r.action }), el("td", { text: r.target }), el("td", { text: r.ip }))); });
      if (!rows.length) tb.appendChild(el("tr", null, el("td", { colspan: "5", class: "empty", text: "Записей нет." })));
      t.appendChild(tb); table.appendChild(t);
    });
  }

  /* ---------- запуск ---------- */
  if (state.tokens) {
    raw("GET", "/api/v1/me", null, true).then(function (me) {
      if (me.role !== "admin") throw new ApiError(403, "");
      state.me = me; renderApp();
    }).catch(function () {
      api("GET", "/api/v1/me").then(function (me) { if (me.role !== "admin") throw new ApiError(403, ""); state.me = me; renderApp(); })
        .catch(function () { state.tokens = null; ssSet("noctis-admin-tokens", null); renderLogin(); });
    });
  } else renderLogin();
})();
