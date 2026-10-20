/* Кабинет Noctis: управление колонкой через API сервера. Чистый JavaScript, без зависимостей.
   Все строки из ответов сервера выводятся только как текст (textContent), разметка из данных сервера не создаётся. */
(function () {
  "use strict";
  var I = window.NOCTIS_I18N || {}, T = I.dash || {}, LANG = I.lang || "ru";
  var CFG = window.NOCTIS_CONFIG || {};
  var root = document.getElementById("dash");
  if (!root) return;

  function t(key, vars) {
    var s = T[key] === undefined ? key : T[key];
    if (vars) for (var k in vars) s = s.replace("{" + k + "}", vars[k]);
    return s;
  }
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
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function field(label, input) { return el("label", { class: "field" }, el("span", { text: label }), input); }
  function option(value, text, selected) { return el("option", { value: value, text: text, selected: selected ? true : false }); }

  /* ---------- хранилище (может быть недоступно) ---------- */
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* нет хранилища */ } }
  function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function ssSet(k, v) { try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) { /* нет хранилища */ } }

  /* Режим проверки (для владельца): 7 быстрых нажатий на логотип открывают поле «Адрес сервера». */
  function devMode() { return !!CFG.showServerField || lsGet("noctis-dev") === "1"; }
  var state = {
    server: (devMode() && lsGet("noctis-server")) || (CFG.defaultServer || (/^https?:/.test(location.protocol) ? location.origin : "http://localhost:8000")),
    tokens: null, user: null, devices: [], deviceId: lsGet("noctis-device"), catalog: null, tab: "speaker", timers: []
  };
  var pollers = [];

  function loadTokens() {
    var raw = lsGet("noctis-tokens") || ssGet("noctis-tokens");
    try { state.tokens = raw ? JSON.parse(raw) : null; } catch (e) { state.tokens = null; }
  }
  function saveTokens(tokens, remember) {
    state.tokens = tokens;
    lsSet("noctis-tokens", null); ssSet("noctis-tokens", null);
    if (tokens) (remember ? lsSet : ssSet)("noctis-tokens", JSON.stringify(tokens));
  }
  function wasRemembered() { return !!lsGet("noctis-tokens"); }

  /* ---------- API ---------- */
  function ApiError(status, detail) { this.status = status; this.detail = detail; }
  function detailText(d) {
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(function (x) { return (x.loc ? x.loc.slice(1).join(".") + ": " : "") + (x.msg || ""); }).join("; ");
    return "";
  }
  function baseUrl() { return state.server.replace(/\/+$/, ""); }

  async function raw(method, path, body, auth, extra) {
    var headers = {};
    if (auth && state.tokens) headers.Authorization = "Bearer " + state.tokens.access_token;
    var opts = { method: method, headers: headers };
    if (body !== undefined && body !== null) {
      if (body instanceof Blob) { opts.body = body; headers["Content-Type"] = "application/octet-stream"; }
      else { opts.body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
    }
    var r;
    try { r = await fetch(baseUrl() + path, opts); }
    catch (e) { throw new ApiError(0, ""); }
    if (r.status === 204) return null;
    var data = null;
    try { data = await r.json(); } catch (e) { /* пустой ответ */ }
    if (!r.ok) throw new ApiError(r.status, data ? detailText(data.detail) : "");
    return data;
  }
  var refreshing = null;
  async function refresh() {
    if (!state.tokens) throw new ApiError(401, "");
    if (!refreshing) refreshing = raw("POST", "/api/v1/auth/refresh", { refresh_token: state.tokens.refresh_token }, false)
      .then(function (pair) { saveTokens(pair, wasRemembered() || !!lsGet("noctis-remember")); })
      .finally(function () { refreshing = null; });
    return refreshing;
  }
  async function api(method, path, body) {
    try { return await raw(method, path, body, true); }
    catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401 || !state.tokens) throw e;
      try { await refresh(); } catch (e2) { logout(true); throw e; }
      return raw(method, path, body, true);
    }
  }
  function errorText(e) {
    if (!(e instanceof ApiError)) return t("err_generic");
    if (e.status === 0) { if (window.console) console.warn("Noctis: no connection to " + baseUrl()); return t("err_network"); }
    var base = { 401: t("err_401"), 403: t("err_403"), 404: t("err_404"), 409: t("err_409"), 429: t("err_429"), 502: t("err_502"), 503: t("err_503") }[e.status] || t("err_generic");
    return e.detail ? base + " (" + e.detail + ")" : base;
  }

  /* ---------- сообщения ---------- */
  var msgBox = el("div", { role: "status", "aria-live": "polite" });
  function say(text, kind) {
    clear(msgBox);
    if (text) msgBox.appendChild(el("div", { class: "msg " + (kind || "ok"), text: text }));
  }
  function fail(e) { say(errorText(e), "err"); }
  async function run(fn) { try { say(""); return await fn(); } catch (e) { fail(e); return undefined; } }

  /* ---------- скрытый режим проверки: 7 быстрых нажатий на логотип ---------- */
  (function () {
    var logo = document.querySelector(".logo");
    if (!logo) return;
    var taps = 0, timer = null;
    logo.addEventListener("click", function (ev) {
      ev.preventDefault();               // одиночное нажатие по-прежнему ведёт на главную, но через 0,8 с
      taps += 1;
      clearTimeout(timer);
      if (taps >= 7) {
        taps = 0;
        lsSet("noctis-dev", "1");
        if (!state.tokens) renderAuth("login");
        say(t("dev_on"), "ok");
        return;
      }
      timer = setTimeout(function () { var n = taps; taps = 0; if (n < 7) window.location.href = logo.getAttribute("href"); }, 800);
    });
  })();

  /* ---------- вход ---------- */
  function renderAuth(mode) {
    stopPolling();
    clear(root);
    mode = mode || "login";
    var server = el("input", { type: "url", value: state.server, required: true, autocomplete: "url", inputmode: "url" });
    var email = el("input", { type: "email", required: true, autocomplete: "email" });
    var pass = el("input", { type: "password", required: true, minlength: mode === "register" ? "8" : false, autocomplete: mode === "register" ? "new-password" : "current-password" });
    var name = el("input", { type: "text", maxlength: "80", autocomplete: "nickname" });
    var consent = el("input", { type: "checkbox", required: true });
    var remember = el("input", { type: "checkbox" });
    remember.checked = wasRemembered();
    var showPw = el("input", { type: "checkbox", onchange: function () { pass.type = showPw.checked ? "text" : "password"; } });
    var submit = el("button", { class: "btn btn--primary", type: "submit", text: mode === "register" ? t("create_account") : t("login") });
    var testBtn = el("button", { class: "btn btn--sm", type: "button", text: t("test_conn"), onclick: async function () {
      var url = server.value.trim();
      if (!/^https?:\/\/[^\s/]+/i.test(url)) { say(t("conn_bad"), "err"); return; }
      testBtn.disabled = true; say("");
      try {
        var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 5000);
        var r = await fetch(url.replace(/\/+$/, "") + "/health", { signal: ctl.signal });
        clearTimeout(timer);
        say(r.ok ? t("conn_ok") : t("conn_fail"), r.ok ? "ok" : "err");
      } catch (e) { say(t("conn_fail"), "err"); }
      testBtn.disabled = false;
    } });
    var resetBtn = el("button", { class: "btn btn--sm", type: "button", text: t("reset_default"), onclick: function () {
      lsSet("noctis-server", null); lsSet("noctis-dev", null);
      state.server = (CFG.defaultServer || (/^https?:/.test(location.protocol) ? location.origin : "http://localhost:8000"));
      renderAuth(mode);
    } });
    var form = el("form", { class: "panel auth", novalidate: false },
      el("h2", { text: mode === "register" ? t("tab_register") : t("tab_login") }),
      el("div", { class: "tabs", role: "tablist" },
        el("button", { type: "button", class: "tab", role: "tab", "aria-selected": mode === "login" ? "true" : "false", text: t("tab_login"), onclick: function () { renderAuth("login"); } }),
        el("button", { type: "button", class: "tab", role: "tab", "aria-selected": mode === "register" ? "true" : "false", text: t("tab_register"), onclick: function () { renderAuth("register"); } })),
      msgBox,
      devMode() ? field(t("server"), server) : null,
      devMode() ? el("div", { class: "row" }, testBtn, resetBtn) : null,
      devMode() ? el("p", { class: "muted", text: t("dev_hint") }) : null,
      mode === "register" ? field(t("display_name"), name) : null,
      field(t("email"), email), field(t("password"), pass),
      el("label", { class: "check" }, showPw, el("span", { text: t("show_password") })),
      mode === "register" ? el("label", { class: "check" }, consent, el("span", { text: t("consent") })) : null,
      el("label", { class: "check" }, remember, el("span", { text: t("remember") })),
      submit);
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (devMode()) {
        var typed = server.value.trim();
        if (!/^https?:\/\/[^\s/]+/i.test(typed)) { say(t("conn_bad"), "err"); return; }
        state.server = typed; lsSet("noctis-server", state.server);
      }
      submit.disabled = true;
      await run(async function () {
        var pair = mode === "register"
          ? await raw("POST", "/api/v1/auth/register", { email: email.value.trim(), password: pass.value, display_name: name.value.trim(), consent: consent.checked, language: LANG }, false)
          : await raw("POST", "/api/v1/auth/login", { email: email.value.trim(), password: pass.value }, false);
        lsSet("noctis-remember", remember.checked ? "1" : null);
        saveTokens(pair, remember.checked);
        await start();
      });
      submit.disabled = false;
    });
    root.appendChild(form);
  }

  function logout(expired) {
    var tokens = state.tokens;
    if (tokens && !expired) raw("POST", "/api/v1/auth/logout", { refresh_token: tokens.refresh_token }, true).catch(function () { /* уже вышли */ });
    saveTokens(null, false);
    state.user = null; state.devices = []; state.catalog = null;
    renderAuth("login");
    if (expired) say(t("err_401"), "err");
  }

  /* ---------- каркас ---------- */
  function stopPolling() { pollers.forEach(clearInterval); pollers = []; }
  function every(ms, fn) { pollers.push(setInterval(fn, ms)); }
  function currentDevice() { return state.devices.filter(function (d) { return d.public_id === state.deviceId; })[0] || null; }

  async function start() {
    var res = await Promise.all([api("GET", "/api/v1/me"), api("GET", "/api/v1/devices"),
      raw("GET", "/api/v1/personalization/catalog?language=" + encodeURIComponent(LANG), null, false)]);
    state.user = res[0]; state.devices = res[1]; state.catalog = res[2];
    if (!currentDevice() && state.devices.length) state.deviceId = state.devices[0].public_id;
    renderApp();
  }

  var TABS = ["speaker", "timers", "music", "history", "privacy"];
  function renderApp() {
    stopPolling();
    clear(root);
    var devSel = el("select", { "aria-label": t("device"), onchange: function () { state.deviceId = devSel.value; lsSet("noctis-device", state.deviceId); renderTab(); } });
    state.devices.forEach(function (d) { devSel.appendChild(option(d.public_id, d.name + (d.online ? " ●" : ""), d.public_id === state.deviceId)); });
    var tabs = el("div", { class: "tabs", role: "tablist" });
    TABS.forEach(function (id) {
      tabs.appendChild(el("button", { class: "tab", role: "tab", "aria-selected": state.tab === id ? "true" : "false", text: t("tab_" + id),
        onclick: function () { state.tab = id; renderApp(); } }));
    });
    var body = el("div", { id: "tab-body" });
    root.appendChild(el("div", { class: "bar" },
      el("h2", { text: t("welcome", { name: state.user.display_name || state.user.email }) }),
      el("div", { class: "row" }, state.devices.length ? devSel : null,
        el("button", { class: "btn btn--sm", text: t("logout"), onclick: function () { logout(false); } }))));
    root.appendChild(msgBox);
    if (!state.devices.length) {
      root.appendChild(el("div", { class: "panel" }, el("h3", { text: t("no_devices") }), el("p", { class: "muted", text: t("no_devices_hint") })));
      root.appendChild(privacyPanel());
      return;
    }
    root.appendChild(tabs); root.appendChild(body);
    renderTab();
    every(8000, async function () {
      try { state.devices = await api("GET", "/api/v1/devices"); var d = currentDevice(); var s = document.getElementById("dev-status"); if (s && d) setStatus(s, d); } catch (e) { /* следующая попытка */ }
    });
  }
  function renderTab() {
    stopPollingTab();
    var body = document.getElementById("tab-body");
    if (!body) return;
    clear(body);
    ({ speaker: tabSpeaker, timers: tabTimers, music: tabMusic, history: tabHistory, privacy: tabPrivacy })[state.tab](body, currentDevice());
  }
  var tabPollers = [];
  function stopPollingTab() { tabPollers.forEach(clearInterval); tabPollers = []; }
  function tabEvery(ms, fn) { tabPollers.push(setInterval(fn, ms)); pollers.push(tabPollers[tabPollers.length - 1]); }
  function setStatus(node, d) { node.className = "status " + (d.online ? "on" : "off"); node.textContent = d.online ? t("online") : t("offline"); }
  function path(d, tail) { return "/api/v1/devices/" + encodeURIComponent(d.public_id) + tail; }
  function utc(iso) { return typeof iso === "string" && !/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso + "Z" : iso; }  // сервер отдаёт UTC без суффикса
  function fmtTime(iso) { var x = new Date(utc(iso)); return isNaN(x) ? "" : x.toLocaleString(LANG); }
  function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60, p = function (n) { return (n < 10 ? "0" : "") + n; };
    return (h ? h + ":" + p(m) : m) + ":" + p(s);
  }

  /* ---------- колонка ---------- */
  function tabSpeaker(body, d) {
    var cat = state.catalog, s = d.settings;
    var status = el("span", { id: "dev-status" }); setStatus(status, d);
    var name = el("input", { type: "text", maxlength: "80", value: d.name });
    var homeCity = el("input", { type: "text", maxlength: "80", value: s.home_city || "", autocomplete: "address-level2" });
    var vol = el("input", { type: "range", min: "0", max: "100", value: s.volume, "aria-label": t("volume") });
    var volOut = el("output", { text: s.volume + " %" });
    var lang = el("select"); cat.languages.forEach(function (l) { lang.appendChild(option(l.id, l.name, l.id === s.language)); });
    var gender = el("select", null, option("male", t("voice_male"), s.voice_gender === "male"), option("female", t("voice_female"), s.voice_gender === "female"));
    var chr = el("select"); cat.characters.forEach(function (c) { chr.appendChild(option(c.id, c.name, c.id === s.character_id)); });
    var chrDesc = el("p", { class: "muted" });
    function showDesc() { var c = cat.characters.filter(function (x) { return x.id === chr.value; })[0]; chrDesc.textContent = c ? c.description : ""; }
    chr.addEventListener("change", showDesc); showDesc();
    var wake = el("select"); var known = false;
    cat.wake_words.forEach(function (w) { if (w.id === s.wake_word) known = true; wake.appendChild(option(w.id, w.label + (w.model_ready ? "" : " — " + t("no_model")), w.id === s.wake_word)); });
    if (!known) wake.appendChild(option(s.wake_word, s.wake_word.replace("custom:", ""), true));
    var zones = [];
    try { zones = Intl.supportedValuesOf("timeZone"); } catch (e) { zones = []; }
    if (zones.indexOf(s.timezone) < 0) zones = [s.timezone].concat(zones);
    var tz = el("select", { "aria-label": t("timezone") });
    zones.forEach(function (z) { tz.appendChild(option(z, z, z === s.timezone)); });
    var detect = el("button", { type: "button", class: "btn btn--sm", text: t("tz_detect"), onclick: function () {
      var z = ""; try { z = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { z = ""; }
      if (!z) return;
      if (zones.indexOf(z) < 0) { tz.appendChild(option(z, z, false)); zones.push(z); }
      tz.value = z;
    } });
    vol.addEventListener("input", function () { volOut.textContent = vol.value + " %"; });

    var form = el("form", { class: "panel" },
      el("div", { class: "bar" }, el("h2", { text: d.name }), status),
      el("p", { class: "muted", text: t("firmware") + ": " + (d.firmware_version || "—") + " · " + t("last_seen") + ": " + (d.last_seen ? fmtTime(d.last_seen) : t("never")) }),
      el("div", { class: "grid2" },
        field(t("name"), name),
        field(t("volume"), el("div", { class: "row" }, vol, volOut)),
        field(t("language"), lang), field(t("voice"), gender), field(t("character"), chr), field(t("wake_word"), wake),
        field(t("timezone"), el("div", { class: "row" }, tz, detect)),
        field(t("home_city"), homeCity)),
      el("p", { class: "muted", text: t("home_city_hint") }),
      chrDesc,
      el("div", { class: "row" },
        el("button", { class: "btn btn--primary", type: "submit", text: t("save") }),
        el("button", { class: "btn btn--danger", type: "button", text: t("reboot"), disabled: !d.online,
          onclick: function () { if (!window.confirm(t("reboot_confirm"))) return; run(async function () { await api("POST", path(d, "/reboot")); say(t("rebooted")); }); } }),
        el("button", { class: "btn btn--danger", type: "button", text: t("dev_delete"),
          onclick: function () {
            if (!window.confirm(t("dev_delete_confirm", { name: d.name }) + (d.online ? t("dev_delete_online") : t("dev_delete_offline")))) return;
            run(async function () {
              await api("DELETE", "/api/v1/devices/" + encodeURIComponent(d.public_id));
              state.devices = await api("GET", "/api/v1/devices");
              state.deviceId = state.devices.length ? state.devices[0].public_id : null;
              lsSet("noctis-device", state.deviceId || "");
              renderApp();
              say(t("dev_deleted"));
            });
          } })));
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      run(async function () {
        if (name.value.trim() && name.value.trim() !== d.name) {
          var upd = await api("PATCH", "/api/v1/devices/" + encodeURIComponent(d.public_id), { name: name.value.trim() });
          d.name = upd.name;
        }
        d.settings = await api("PATCH", path(d, "/settings"), { volume: parseInt(vol.value, 10), language: lang.value, voice_gender: gender.value,
          character_id: chr.value, wake_word: wake.value, timezone: tz.value.trim(), home_city: homeCity.value.trim() });
        say(t("saved"));
        renderApp();
      });
    });
    if (!d.online) body.appendChild(el("div", { class: "msg warn", text: t("offline_note") }));
    body.appendChild(form);
    body.appendChild(wifiPanel(d));
  }

  /* Смена сети Wi-Fi у включённой колонки: сервер передаёт команду, колонка при неудаче сама возвращается в прежнюю сеть. */
  function wifiPanel(d) {
    var box = el("section", { class: "panel" }, el("h3", { text: t("wifi_h") }));
    if (!d.online) { box.appendChild(el("p", { class: "muted", text: t("wifi_offline") })); return box; }
    var status = el("div", { role: "status", "aria-live": "polite" });
    function show(text, kind) { clear(status); if (text) status.appendChild(el("div", { class: "msg " + kind, text: text })); }
    var ssid = el("input", { type: "text", maxlength: "32", required: true, autocomplete: "off" });
    var pass = el("input", { type: "password", maxlength: "64", autocomplete: "new-password" });
    var go = el("button", { class: "btn btn--primary", type: "submit", text: t("wifi_apply") });
    var form = el("form", null, el("p", { class: "muted", text: t("wifi_note") }),
      el("div", { class: "grid2" }, field(t("wifi_ssid"), ssid), field(t("wifi_pass"), pass)),
      el("p", { class: "muted", text: t("wifi_open_hint") }), go);
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var name = ssid.value.trim();
      go.disabled = true; show(t("wifi_pending"), "ok");
      try {
        await api("POST", path(d, "/wifi"), { ssid: name, password: pass.value });
        pass.value = "";
      } catch (e) {
        go.disabled = false;
        show(e instanceof ApiError && e.status === 422 ? t("wifi_invalid") : e instanceof ApiError && e.status === 409 && /progress/i.test(e.detail) ? t("wifi_busy") : errorText(e), "err");
        return;
      }
      var tries = 0;
      tabEvery(2000, async function () {
        tries += 1;
        try {
          var st = await api("GET", path(d, "/wifi"));
          if (st.state === "ok") { stopPollingTab(); show(t("wifi_ok", { ssid: name }), "ok"); go.disabled = false; }
          else if (st.state === "failed") { stopPollingTab(); show(t("wifi_failed", { ssid: name }), "err"); go.disabled = false; }
          else if (st.state === "timeout" || tries > 50) { stopPollingTab(); show(t("wifi_timeout"), "err"); go.disabled = false; }
        } catch (e) { /* связь могла прерваться на время смены сети — продолжаем опрос */ }
      });
    });
    box.appendChild(form); box.appendChild(status);
    return box;
  }

  /* ---------- таймеры и будильники ---------- */
  function tabTimers(body, d) {
    var timersBox = el("div", { class: "panel" }, el("p", { class: "muted", text: t("loading") })), alarmsBox = el("div", { class: "panel" });
    body.appendChild(timersBox); body.appendChild(alarmsBox);
    var timers = [], loadedAt = Date.now();

    async function loadTimers() {
      await run(async function () { timers = await api("GET", path(d, "/timers")); loadedAt = Date.now(); drawTimers(); });
    }
    function drawTimers() {
      clear(timersBox);
      var min = el("input", { type: "number", min: "0", max: "1439", value: "5", "aria-label": t("minutes") });
      var sec = el("input", { type: "number", min: "0", max: "59", value: "0", "aria-label": t("seconds") });
      var label = el("input", { type: "text", maxlength: "80", placeholder: t("label"), "aria-label": t("label") });
      var add = el("form", { class: "row", onsubmit: function (ev) {
        ev.preventDefault();
        var total = (parseInt(min.value, 10) || 0) * 60 + (parseInt(sec.value, 10) || 0);
        if (total < 1) return;
        run(async function () { await api("POST", path(d, "/timers"), { seconds: total, label: label.value.trim() }); await loadTimers(); });
      } }, el("span", { text: t("minutes") }), min, el("span", { text: t("seconds") }), sec, label,
      el("button", { class: "btn btn--primary btn--sm", type: "submit", text: t("add_timer") }));
      var quick = el("div", { class: "row", role: "group", "aria-label": t("quick") }, el("span", { class: "muted", text: t("quick") + ":" }));
      [1, 5, 10, 15, 30].forEach(function (m) {
        quick.appendChild(el("button", { class: "btn btn--sm", type: "button", text: m + " " + t("min_short"), onclick: function () {
          run(async function () { await api("POST", path(d, "/timers"), { seconds: m * 60, label: "" }); await loadTimers(); }); } }));
      });
      var list = el("ul", { class: "list" });
      if (!timers.length) list.appendChild(el("li", { class: "empty", text: t("no_timers") }));
      timers.forEach(function (tm) {
        var rem = el("span", { class: "time", "data-id": tm.id });
        rem.textContent = fmtDur(tm.remaining_sec);
        var paused = tm.state === "paused";
        list.appendChild(el("li", null,
          el("span", { class: "grow" }, rem, " ", el("span", { class: "muted", text: tm.label || "" }), paused ? " " : "", paused ? el("span", { class: "badge", text: t("paused") }) : ""),
          el("button", { class: "btn btn--sm", text: paused ? t("resume") : t("pause"), onclick: function () {
            run(async function () { await api("POST", path(d, "/timers/" + tm.id + "/" + (paused ? "resume" : "pause"))); await loadTimers(); }); } }),
          el("button", { class: "btn btn--sm btn--danger", text: t("delete"), onclick: function () {
            run(async function () { await api("DELETE", path(d, "/timers/" + tm.id)); await loadTimers(); }); } })));
      });
      timersBox.appendChild(el("h2", { text: t("timers_title") })); timersBox.appendChild(quick); timersBox.appendChild(add); timersBox.appendChild(list);
    }
    tabEvery(1000, function () {
      var spent = (Date.now() - loadedAt) / 1000;
      timers.forEach(function (tm) {
        if (tm.state !== "running") return;
        var n = timersBox.querySelector('[data-id="' + tm.id + '"]');
        if (n) n.textContent = fmtDur(tm.remaining_sec - spent);
      });
    });
    tabEvery(10000, function () { api("GET", path(d, "/timers")).then(function (x) { timers = x; loadedAt = Date.now(); drawTimers(); }).catch(function () { /* повтор позже */ }); });

    var alarms = [];
    async function loadAlarms() { await run(async function () { alarms = await api("GET", path(d, "/alarms")); drawAlarms(); }); }
    function days(list) {
      if (!list.length) return t("once");
      if (list.length === 7) return t("every_day");
      return list.slice().sort().map(function (i) { return T.days[i]; }).join(", ");
    }
    function drawAlarms() {
      clear(alarmsBox);
      var time = el("input", { type: "time", value: "07:30", required: true, "aria-label": t("time") });
      var label = el("input", { type: "text", maxlength: "80", placeholder: t("label"), "aria-label": t("label") });
      var melody = el("select", { "aria-label": t("melody") });
      ["default", "soft", "rooster", "beeps"].forEach(function (m) { melody.appendChild(option(m, T.melodies[m], false)); });
      var daysBox = el("div", { class: "days", role: "group", "aria-label": t("weekdays") });
      var boxes = T.days.map(function (name, i) {
        var cb = el("input", { type: "checkbox", value: String(i) });
        daysBox.appendChild(el("label", null, cb, el("span", { text: name })));
        return cb;
      });
      var add = el("form", { onsubmit: function (ev) {
        ev.preventDefault();
        var hm = time.value.split(":");
        if (hm.length < 2) return;
        var wd = boxes.filter(function (b) { return b.checked; }).map(function (b) { return parseInt(b.value, 10); });
        run(async function () {
          await api("POST", path(d, "/alarms"), { hour: parseInt(hm[0], 10), minute: parseInt(hm[1], 10), weekdays: wd, label: label.value.trim(), melody: melody.value });
          await loadAlarms();
        });
      } }, el("div", { class: "row" }, time, melody, label), daysBox,
      el("button", { class: "btn btn--primary btn--sm", type: "submit", text: t("add_alarm") }));
      var list = el("ul", { class: "list" });
      if (!alarms.length) list.appendChild(el("li", { class: "empty", text: t("no_alarms") }));
      alarms.forEach(function (a) {
        var p = function (n) { return (n < 10 ? "0" : "") + n; };
        var on = el("input", { type: "checkbox", "aria-label": t("enabled"), onchange: function () {
          run(async function () { await api("PATCH", path(d, "/alarms/" + a.id), { enabled: on.checked }); await loadAlarms(); }); } });
        on.checked = a.enabled;
        list.appendChild(el("li", null,
          el("span", { class: "grow" }, el("span", { class: "time", text: p(a.hour) + ":" + p(a.minute) }), " ",
            el("span", { class: "muted", text: days(a.weekdays) + (a.date ? " · " + a.date : "") + (a.label ? " · " + a.label : "") + " · " + (T.melodies[a.melody] || a.melody) })),
          el("label", { class: "check" }, on, el("span", { text: t("enabled") })),
          el("button", { class: "btn btn--sm btn--danger", text: t("delete"), onclick: function () {
            run(async function () { await api("DELETE", path(d, "/alarms/" + a.id)); await loadAlarms(); }); } })));
      });
      alarmsBox.appendChild(el("h2", { text: t("alarms_title") })); alarmsBox.appendChild(add); alarmsBox.appendChild(list);
    }
    loadTimers(); loadAlarms();
  }

  /* ---------- музыка ---------- */
  function tabMusic(body, d) {
    if (!d.online) body.appendChild(el("div", { class: "msg warn", text: t("offline_note") }));
    var nowBox = el("div", { class: "panel" }), findBox = el("div", { class: "panel" }), resBox = el("div", { class: "panel" }), radioBox = el("div", { class: "panel" });
    body.appendChild(nowBox); body.appendChild(findBox); body.appendChild(resBox); body.appendChild(radioBox);

    function drawNow(info) {
      clear(nowBox);
      nowBox.appendChild(el("h2", { text: t("now_playing") }));
      var tr = info && info.track;
      if (!tr || info.state === "stopped") { nowBox.appendChild(el("p", { class: "muted", text: t("nothing_playing") })); return; }
      var playing = info.state === "playing";
      nowBox.appendChild(el("div", { class: "now" },
        el("div", { class: "grow" }, el("b", { text: tr.title }), el("div", { class: "muted", text: (tr.artist || "") + " · " + (info.index + 1) + "/" + info.total })),
        el("div", { class: "row" },
          ctl("prev", t("prev")), ctl(playing ? "pause" : "resume", playing ? t("pause") : t("resume")), ctl("next", t("next")), ctl("stop", t("stop")))));
    }
    function ctl(action, text) {
      return el("button", { class: "btn btn--sm", text: text, onclick: function () {
        run(async function () { drawNow(await api("POST", path(d, "/player/" + action))); }); } });
    }
    async function pollNow() { try { drawNow(await api("GET", path(d, "/player"))); } catch (e) { /* повтор */ } }

    var source = el("select", { "aria-label": t("source") }, option("library", t("source_library"), true), option("radio", t("source_radio"), false), option("jamendo", t("source_jamendo"), false));
    var q = el("input", { type: "search", maxlength: "100", placeholder: t("query"), "aria-label": t("query") });
    var file = el("input", { type: "file", accept: ".mp3,.flac,.ogg,.wav,audio/*", multiple: true, "aria-label": t("upload") });
    findBox.appendChild(el("h2", { text: t("find_music") }));
    var findForm = el("form", { class: "row", onsubmit: function (ev) { ev.preventDefault(); search(); } }, source, q, el("button", { class: "btn btn--primary btn--sm", type: "submit", text: t("search") }));
    findBox.appendChild(findForm);
    findBox.appendChild(el("div", { class: "row" }, field(t("upload"), file),
      el("button", { class: "btn btn--sm", type: "button", text: t("upload_go"), onclick: upload })));

    function drawTracks(list, deletable) {
      clear(resBox);
      resBox.appendChild(el("h2", { text: t("results") }));
      var ul = el("ul", { class: "list" });
      if (!list.length) ul.appendChild(el("li", { class: "empty", text: t("no_tracks") }));
      list.forEach(function (tr) {
        ul.appendChild(el("li", null,
          el("span", { class: "grow" }, el("b", { text: tr.title }), " ", el("span", { class: "muted", text: [tr.artist, tr.license].filter(Boolean).join(" · ") })),
          el("button", { class: "btn btn--primary btn--sm", text: t("play"), disabled: !d.online, onclick: function () {
            run(async function () { drawNow(await api("POST", path(d, "/player/play"), { track_id: tr.id })); }); } }),
          deletable ? el("button", { class: "btn btn--sm btn--danger", text: t("delete"), onclick: function () {
            run(async function () { await api("DELETE", deletable === "radio" ? "/api/v1/media/radio/" + tr.id : "/api/v1/media/tracks/" + tr.id); await search(); await drawRadio(); }); } }) : null));
      });
      resBox.appendChild(ul);
    }
    async function search() {
      await run(async function () {
        var s = source.value, term = q.value.trim(), list;
        if (s === "jamendo") list = await api("GET", "/api/v1/media/jamendo/search?q=" + encodeURIComponent(term));
        else if (s === "radio") list = (await api("GET", "/api/v1/media/radio")).filter(function (x) { return !term || x.title.toLowerCase().indexOf(term.toLowerCase()) >= 0; });
        else list = await api("GET", "/api/v1/media/tracks?source=local&q=" + encodeURIComponent(term));
        drawTracks(list, s === "library" ? "library" : s === "radio" ? "radio" : false);
      });
    }
    async function upload() {
      var files = Array.prototype.slice.call(file.files || []);
      if (!files.length) return;
      await run(async function () {
        for (var i = 0; i < files.length; i++) await api("PUT", "/api/v1/media/library/" + encodeURIComponent(files[i].name), files[i]);
        file.value = ""; say(t("uploaded", { n: files.length }));
        source.value = "library"; await search();
      });
    }
    async function drawRadio() {
      var stations = []; try { stations = await api("GET", "/api/v1/media/radio"); } catch (e) { fail(e); }
      clear(radioBox);
      var nm = el("input", { type: "text", maxlength: "100", required: true, placeholder: t("station_name"), "aria-label": t("station_name") });
      var url = el("input", { type: "url", maxlength: "500", required: true, placeholder: "https://…/stream.mp3", "aria-label": t("station_url") });
      radioBox.appendChild(el("h2", { text: t("add_station") }));
      radioBox.appendChild(el("p", { class: "muted", text: t("radio_hint") }));
      radioBox.appendChild(el("form", { class: "row", onsubmit: function (ev) {
        ev.preventDefault();
        run(async function () { await api("POST", "/api/v1/media/radio", { name: nm.value.trim(), url: url.value.trim() }); await drawRadio(); say(t("saved")); });
      } }, nm, url, el("button", { class: "btn btn--sm", type: "submit", text: t("add_station") })));
      var ul = el("ul", { class: "list" });
      stations.forEach(function (st) { ul.appendChild(el("li", null, el("span", { class: "grow", text: st.title }))); });
      radioBox.appendChild(ul);
    }
    drawNow(null); pollNow(); drawRadio(); search();
    tabEvery(3000, pollNow);
  }

  /* ---------- история ---------- */
  function tabHistory(body, d) {
    var box = el("div", { class: "panel" }, el("p", { class: "muted", text: t("loading") }));
    body.appendChild(box);
    async function load() {
      await run(async function () {
        var rows = await api("GET", path(d, "/history?limit=50"));
        clear(box);
        box.appendChild(el("div", { class: "bar" }, el("h2", { text: t("tab_history") }),
          el("button", { class: "btn btn--sm btn--danger", text: t("clear_history"), disabled: !rows.length, onclick: function () {
            if (!window.confirm(t("clear_confirm"))) return;
            run(async function () { await api("DELETE", path(d, "/history")); await load(); }); } })));
        var ul = el("ul", { class: "list hist" });
        if (!rows.length) ul.appendChild(el("li", { class: "empty", text: t("no_history") }));
        rows.forEach(function (r) {
          ul.appendChild(el("li", null, el("time", { text: fmtTime(r.created_at) }),
            el("div", { class: "q", text: t("you") + ": " + r.user_text }), el("div", { class: "a", text: t("assistant") + ": " + r.assistant_text })));
        });
        box.appendChild(ul);
      });
    }
    load();
  }

  /* ---------- мои данные (приватность): только по запросу пользователя ---------- */
  async function downloadMyData() {
    async function get() { return fetch(baseUrl() + "/api/v1/me/export", { headers: { Authorization: "Bearer " + state.tokens.access_token } }); }
    var r;
    try { r = await get(); } catch (e) { throw new ApiError(0, ""); }
    if (r.status === 401) { await refresh(); r = await get(); }
    if (!r.ok) throw new ApiError(r.status, "");
    var blob = await r.blob();
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "noctis-data.json" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function privacyPanel() {
    var pw = el("input", { type: "password", autocomplete: "current-password", "aria-label": t("priv_delete_pw"), placeholder: t("priv_delete_pw") });
    return el("div", { class: "panel" },
      el("h2", { text: t("tab_privacy") }),
      el("p", { class: "muted", text: t("priv_intro") }),
      el("div", { class: "row" },
        el("button", { class: "btn btn--primary", type: "button", text: t("priv_export"),
          onclick: function () { run(async function () { await downloadMyData(); say(t("priv_exported")); }); } }),
        el("button", { class: "btn btn--danger", type: "button", text: t("priv_clear"),
          onclick: function () {
            if (!window.confirm(t("priv_clear_confirm"))) return;
            run(async function () { var r = await api("DELETE", "/api/v1/me/history"); say(t("priv_cleared", { n: r.deleted })); });
          } })),
      el("div", { class: "grid2" }, field(t("priv_delete_pw"), pw)),
      el("div", { class: "row" },
        el("button", { class: "btn btn--danger", type: "button", text: t("priv_delete"),
          onclick: function () {
            if (!pw.value) { pw.focus(); return; }
            if (!window.confirm(t("priv_delete_confirm"))) return;
            run(async function () {
              await api("DELETE", "/api/v1/me", { password: pw.value });
              pw.value = "";
              logout(false);
              say(t("priv_deleted"));
            });
          } })));
  }
  function tabPrivacy(body) { body.appendChild(privacyPanel()); }

  /* ---------- запуск ---------- */
  loadTokens();
  if (state.tokens) {
    start().catch(function (e) {
      if (e instanceof ApiError && e.status === 401) { saveTokens(null, false); renderAuth("login"); }
      else { renderAuth("login"); fail(e); }
    });
  } else renderAuth("login");
})();
