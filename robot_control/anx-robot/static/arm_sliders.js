/* anx-arm-sliders — expandable absolute servo sliders under Arm Control buttons. */
(function () {
  var MARKER = "anx-arm-sliders";
  var state = { servos: [], timer: null, busy: false, open: false };

  try {
    state.open = sessionStorage.getItem(MARKER + "-open") === "1";
  } catch (e) {
    state.open = false;
  }

  function css() {
    if (document.getElementById(MARKER + "-css")) return;
    var s = document.createElement("style");
    s.id = MARKER + "-css";
    s.textContent =
      "#" + MARKER + "{" +
      "margin-top:10px;border-radius:6px;background:rgba(0,0,0,.35);" +
      "color:#e8e8e8;font:13px/1.35 system-ui,sans-serif;overflow:hidden}" +
      "#" + MARKER + " > summary{" +
      "list-style:none;cursor:pointer;user-select:none;padding:10px 12px;" +
      "display:flex;align-items:center;justify-content:space-between;gap:8px;" +
      "font-weight:600;letter-spacing:.02em}" +
      "#" + MARKER + " > summary::-webkit-details-marker{display:none}" +
      "#" + MARKER + " > summary .chev{" +
      "opacity:.75;font-size:11px;transition:transform .15s ease}" +
      "#" + MARKER + "[open] > summary .chev{transform:rotate(90deg)}" +
      "#" + MARKER + " .body{padding:0 12px 12px}" +
      "#" + MARKER + " .row{display:grid;grid-template-columns:88px 1fr 44px;gap:8px;" +
      "align-items:center;margin:6px 0}" +
      "#" + MARKER + " label{opacity:.9}" +
      "#" + MARKER + " input[type=range]{width:100%;accent-color:#7dcea0}" +
      "#" + MARKER + " .deg{font-variant-numeric:tabular-nums;text-align:right;opacity:.95}" +
      "#" + MARKER + " .hint{opacity:.65;font-size:11px;margin-top:8px}" +
      "#" + MARKER + " .err{color:#e74c3c;font-size:11px;margin-top:6px}";
    document.head.appendChild(s);
  }

  function findArmSheet() {
    // Prefer ControllerSheet title node (.mod-title).
    var titles = document.querySelectorAll(".mod-title");
    for (var i = 0; i < titles.length; i++) {
      if ((titles[i].textContent || "").trim() === "Arm Control") {
        return titles[i].parentElement || titles[i];
      }
    }
    // Fallback: any element whose trimmed text is exactly "Arm Control".
    var nodes = document.querySelectorAll("h1,h2,h3,h4,h5,h6,div,span,p");
    for (var j = 0; j < nodes.length; j++) {
      var el = nodes[j];
      if ((el.textContent || "").trim() === "Arm Control") {
        return el.closest(".v-card, .v-sheet, [class*='sheet'], [class*='Sheet']")
          || el.parentElement
          || el;
      }
    }
    return null;
  }

  function mountPoint(sheet) {
    if (!sheet) return null;
    var wrap = sheet.querySelector(".mod-wrapper");
    // Place after the button wrapper (below buttons), still inside the sheet.
    return wrap || sheet;
  }

  function ensurePanel(anchor) {
    css();
    var box = document.getElementById(MARKER);
    if (box && box.isConnected) {
      // Keep it after the button block if Vue moved siblings around.
      if (anchor && box.previousElementSibling !== anchor && box.parentElement === anchor.parentElement) {
        if (anchor.nextSibling !== box) {
          anchor.parentElement.insertBefore(box, anchor.nextSibling);
        }
      }
      return box;
    }
    if (box && !box.isConnected) {
      box.remove();
    }
    box = document.createElement("details");
    box.id = MARKER;
    if (state.open) box.setAttribute("open", "");
    box.innerHTML =
      "<summary><span>Servo positions</span><span class=\"chev\">▸</span></summary>" +
      '<div class="body"><div class="hint">Loading…</div></div>';
    box.addEventListener("toggle", function () {
      state.open = box.open;
      try {
        sessionStorage.setItem(MARKER + "-open", state.open ? "1" : "0");
      } catch (e) {}
      if (box.open && !(state.servos && state.servos.length)) {
        refresh();
      }
    });
    if (anchor && anchor.parentElement) {
      anchor.parentElement.insertBefore(box, anchor.nextSibling);
    } else if (anchor) {
      anchor.appendChild(box);
    }
    return box;
  }

  function renderRows() {
    var box = document.getElementById(MARKER);
    if (!box) return;
    var body = box.querySelector(".body");
    if (!body) return;
    if (!state.servos.length) {
      body.innerHTML = '<div class="err">No servo channels from /anx/servos</div>';
      return;
    }
    var html = "";
    state.servos.forEach(function (s) {
      html +=
        '<div class="row" data-ch="' + s.channel + '">' +
        "<label>" + s.name + "</label>" +
        '<input type="range" min="' + s.min + '" max="' + s.max +
        '" step="1" value="' + s.deg + '" />' +
        '<span class="deg">' + s.deg + "°</span></div>";
    });
    html +=
      '<div class="hint">Mechanical limits (hardcoded). Drag to move; values clamp to safe range.</div>';
    body.innerHTML = html;
    body.querySelectorAll("input[type=range]").forEach(function (input) {
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
    if (state.busy) {
      if (state.timer) clearTimeout(state.timer);
      state.timer = setTimeout(function () {
        postAngle(channel, deg);
      }, 80);
      return;
    }
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
              if (input && document.activeElement !== input) input.value = String(body.deg);
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

  function refresh() {
    return fetch("/anx/servos", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (body) {
        state.servos = body.servos || [];
        renderRows();
      })
      .catch(function (err) {
        console.warn("anx arm sliders load failed:", err);
        var box = document.getElementById(MARKER);
        var body = box && box.querySelector(".body");
        if (body) {
          body.innerHTML =
            '<div class="err">Could not load /anx/servos — is the overlay API up?</div>';
        }
      });
  }

  function attach() {
    var sheet = findArmSheet();
    if (!sheet) return false;
    var anchor = mountPoint(sheet);
    var box = ensurePanel(anchor);
    if (!box) return false;
    if (state.open || box.open) {
      refresh();
    } else if (!state.servos.length) {
      // Prefetch so expand is instant.
      refresh();
    }
    return true;
  }

  function boot() {
    if (attach()) {
      // Vue may re-render ControllerSheet and drop our node — keep it mounted.
      var obs = new MutationObserver(function () {
        if (!document.getElementById(MARKER) || !document.getElementById(MARKER).isConnected) {
          attach();
        }
      });
      obs.observe(document.documentElement, { childList: true, subtree: true });
      return;
    }
    var tries = 0;
    var obsWait = new MutationObserver(function () {
      if (attach() || ++tries > 80) {
        obsWait.disconnect();
        if (document.getElementById(MARKER)) {
          var obs = new MutationObserver(function () {
            if (!document.getElementById(MARKER) || !document.getElementById(MARKER).isConnected) {
              attach();
            }
          });
          obs.observe(document.documentElement, { childList: true, subtree: true });
        }
      }
    });
    obsWait.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(function () {
      obsWait.disconnect();
      attach();
    }, 20000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
