/* Тема (тёмная по умолчанию, светлая по выбору), мобильное меню, переключатель языка. Без зависимостей. */
(function () {
  var root = document.documentElement;

  function stored(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function store(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* приватный режим */ } }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#05030A" : "#F6F3FD");
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }

  apply(stored("noctis-theme") === "light" ? "light" : "dark");
  document.addEventListener("DOMContentLoaded", function () {
    apply(root.getAttribute("data-theme"));
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      store("noctis-theme", next);
      apply(next);
    });

    var burger = document.getElementById("burger"), nav = document.getElementById("nav");
    if (burger && nav) burger.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });

    // Alt+X открывает страницу администратора (нет в меню; доступ защищён входом на сервере)
    document.addEventListener("keydown", function (e) {
      if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === "KeyX" || e.key === "x" || e.key === "X" || e.key === "ч" || e.key === "Ч")) {
        var m = document.querySelector('meta[name="noctis-admin"]');
        if (m && !/admin\.html$/.test(window.location.pathname)) { e.preventDefault(); window.location.href = m.getAttribute("content"); }
      }
    });

    var lang = document.getElementById("lang-select");
    if (lang) lang.addEventListener("change", function () {
      store("noctis-lang", lang.value);
      window.location.href = lang.options[lang.selectedIndex].getAttribute("data-href");
    });
  });
})();
