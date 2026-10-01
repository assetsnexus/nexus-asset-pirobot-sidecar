/* anx-power-badge — estimated load watts in stock "Hard Ware" (from voltage sag). */
(function () {
  var MARKER = "anx-power-badge";
  if (document.getElementById(MARKER)) return;

  function css() {
    if (document.getElementById(MARKER + "-css")) return;
    var s = document.createElement("style");
    s.id = MARKER + "-css";
    s.textContent =
      "#" + MARKER + "{" +
      "margin-top:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}" +
      "#" + MARKER + " .badge{" +
      "min-width:7.5rem;padding:10px 14px;border-radius:8px;font:700 18px/1.1 ui-monospace,monospace;" +
      "color:#111;text-align:center;box-shadow:inset 0 0 0 1px rgba(0,0,0,.15)}" +
      "#" + MARKER + " .badge.ok{background:#7dcea0}" +
      "#" + MARKER + " .badge.warn{background:#f4d03f}" +
      "#" + MARKER + " .badge.crit{background:#e74c3c;color:#fff;animation:anxPulse 0.9s ease-in-out infinite}" +
      "#" + MARKER + " .badge.unknown{background:#95a5a6}" +
      "#" + MARKER + " .meta{font:12px/1.35 system-ui,sans-serif;opacity:.85;color:inherit}" +
      "@keyframes anxPulse{0%,100%{filter:brightness(1)}50%{filter:brightness(1.15)}}";
    document.head.appendChild(s);
  }

  function findHardwareSheet() {
    var nodes = document.querySelectorAll("h1,h2,h3,h4,h5,h6,div,span,p");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if ((el.textContent || "").trim() === "Hard Ware") {
        return el.closest(".v-card, .v-sheet, [class*='sheet'], [class*='Sheet']") || el.parentElement || el;
      }
    }
    return null;
  }

  function ensure(root) {
    css();
    var box = document.getElementById(MARKER);
    if (!box) {
      box = document.createElement("div");
      box.id = MARKER;
      box.innerHTML =
        '<div class="badge unknown">— W</div>' +
        '<div class="meta">Load est. from battery sag (no current shunt). High W ≈ stalled servo.</div>';
      root.appendChild(box);
    }
    return box;
  }

  function paint(body) {
    var box = document.getElementById(MARKER);
    if (!box) return;
    var badge = box.querySelector(".badge");
    var meta = box.querySelector(".meta");
    if (!badge) return;
    if (!body || !body.available) {
      badge.className = "badge unknown";
      badge.textContent = "n/a";
      if (meta) meta.textContent = "No battery ADC — cannot estimate watts.";
      return;
    }
    var level = body.level || "unknown";
    badge.className = "badge " + level;
    badge.textContent = (body.power_w_est != null ? body.power_w_est.toFixed(1) : "—") + " W";
    if (meta) {
      meta.textContent =
        "≈" +
        (body.current_a_est != null ? body.current_a_est.toFixed(2) : "—") +
        " A · " +
        (body.volts != null ? body.volts.toFixed(2) : "—") +
        " V (sag " +
        (body.sag_v != null ? body.sag_v.toFixed(3) : "—") +
        " V) · " +
        (level === "crit" ? "OVERLOAD?" : level === "warn" ? "high load" : "ok");
    }
  }

  function tick() {
    fetch("/anx/power", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(paint)
      .catch(function () {
        paint(null);
      });
  }

  function boot() {
    var sheet = findHardwareSheet();
    if (!sheet) return false;
    ensure(sheet);
    tick();
    setInterval(tick, 500);
    return true;
  }

  function start() {
    if (boot()) return;
    var tries = 0;
    var obs = new MutationObserver(function () {
      if (boot() || ++tries > 40) obs.disconnect();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(function () {
      obs.disconnect();
      boot();
    }, 15000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
