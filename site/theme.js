/* Day / Night / Auto colour switch, shared by every page. Loaded in <head> so the page never flashes. */
(function () {
  "use strict";
  var KEY = "omega.theme";
  function get() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
  function apply(mode) {
    var r = document.documentElement;
    if (mode === "day") r.setAttribute("data-theme", "light");
    else if (mode === "night") r.setAttribute("data-theme", "dark");
    else r.removeAttribute("data-theme");
  }
  function paint() {
    var mode = get();
    document.querySelectorAll("[data-theme-switch] button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === mode)); });
  }
  function set(mode) {
    try { localStorage.setItem(KEY, mode); } catch (e) { /* storage unavailable */ }
    apply(mode); paint();
    window.dispatchEvent(new Event("omega-theme"));
  }
  apply(get());
  window.OmegaTheme = { get: get, set: set };
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-switch]").forEach(function (box) {
      box.innerHTML = '<button type="button" data-mode="day" title="Day colours" aria-label="Day colours">☀️<span class="tl"> Day</span></button>' +
        '<button type="button" data-mode="night" title="Night colours" aria-label="Night colours">🌙<span class="tl"> Night</span></button>' +
        '<button type="button" data-mode="auto" title="Follow this device" aria-label="Follow this device">A<span class="tl">uto</span></button>';
      box.addEventListener("click", function (ev) { var b = ev.target.closest("button[data-mode]"); if (b) set(b.dataset.mode); });
    });
    paint();
  });
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (get() === "auto") window.dispatchEvent(new Event("omega-theme")); });
  } catch (e) { /* old browser */ }
})();
