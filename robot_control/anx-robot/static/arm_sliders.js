/* anx-arm-sliders — absolute servo sliders under stock "Arm Control". */
(function () {
  var MARKER = "anx-arm-sliders";
  if (document.getElementById(MARKER)) return;

  var state = { servos: [], timer: null, busy: false };

  function css() {
    if (document.getElementById(MARKER + "-css")) return;
    var s = document.createElement("style");
    s.id = MARKER + "-css";
    s.textContent =
      "#" + MARKER + "{" +
      "margin-top:12px;padding:10px 12px;border-radius:6px;" +
      "background:rgba(0,0,0,.35);color:#e8e8e8;font:13px/1.35 system-ui,sans-serif}" +
      "#" + MARKER + " h4{margin:0 0 8px;font-size:13px;font-weight:600;letter-spacing:.02em}" +
      "#" + MARKER + " .row{display:grid;grid-template-columns:88px 1fr 44px;gap:8px;" +
      "align-items:center;margin:6px 0}" +
      "#" + MARKER + " label{opacity:.9}" +
      "#" + MARKER + " input[type=range]{width:100%}" +
      "#" + MARKER + " .deg{font-variant-numeric:tabular-nums;text-align:right;opacity:.95}" +
      "#" + MARKER + " .hint{opacity:.65;font-size:11px;margin-top:6px}";
    document.head.appendChild(s);
  }

  function findArmSheet() {
    var nodes = document.querySelectorAll("h1,h2,h3,h4,h5,h6,div,span,p");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if ((el.textContent || "").trim() === "Arm Control") {
        var sheet = el.closest(".v-card, .v-sheet, [class*='sheet'], [class*='Sheet']") || el.parentElement;
        return sheet || el;
      }
    }
    return null;
  }

  function render(root) {
    css();
    var box = document.getElementById(MARKER);
    if (!box) {
      box = document.createElement("div");
      box.id = MARKER;
      root.appendChild(box);
    }
    var html = "<h4>Arm positions</h4>";
    state.servos.forEach(function (s) {
      html +=
        '<div class="row" data-ch="' + s.channel + '">' +
        "<label>" + s.name + "</label>" +
        '<input type="range" min="' + s.min + '" max="' + s.max + '" step="1" value="' + s.deg + '" />' +
        '<span class="deg">' + s.deg + "°</span></div>";
    });
    html += '<div class="hint">Limits are mechanical (hardcoded). Drag to move; values clamp to safe range.</div>';
    box.innerHTML = html;
    box.querySelectorAll("input[type=range]").forEach(function (input) {
      input.addEventListener("input", onSlide);
      input.addEventListener("change", onSlide);
    });
  }

  function onSlide(ev) {
    var input = ev.target;
    var row = input.closest("[data-ch]");
    if (!row) return;
    var ch = Number(row.getAttribute("data-ch"));
    var deg = Number(input.value);
    var degEl = row.querySelector(".deg");
    if (degEl) degEl.textContent = deg + "°";
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      postAngle(ch, deg);
    }, 60);
  }

  function postAngle(channel, deg) {
    if (state.busy) return;
    state.busy = true;
    fetch("/anx/servos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: channel, deg: deg }),
      cache: "no-store",
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || "http " + r.status);
          if (body.deg != null) {
            var row = document.querySelector("#" + MARKER + ' [data-ch="' + channel + '"]');
            if (row) {
              var input = row.querySelector("input");
              var degEl = row.querySelector(".deg");
              if (input) input.value = String(body.deg);
              if (degEl) degEl.textContent = body.deg + "°";
            }
          }
        });
      })
      .catch(function (err) {
        console.warn("anx arm slider:", err);
      })
      .finally(function () {
        state.busy = false;
      });
  }

  function loadAndMount() {
    var sheet = findArmSheet();
    if (!sheet) return false;
    fetch("/anx/servos", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (body) {
        state.servos = body.servos || [];
        render(sheet);
      })
      .catch(function (err) {
        console.warn("anx arm sliders load failed:", err);
      });
    return true;
  }

  function boot() {
    if (loadAndMount()) return;
    var tries = 0;
    var obs = new MutationObserver(function () {
      if (loadAndMount() || ++tries > 40) obs.disconnect();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(function () {
      obs.disconnect();
      loadAndMount();
    }, 15000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
