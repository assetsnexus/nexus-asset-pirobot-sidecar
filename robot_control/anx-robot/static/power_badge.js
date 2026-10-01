/* Hard Ware: latency + battery last-7s sparklines (client RTT + /anx/power SoC). */
(function () {
  var MARKER = "anx-hw-series";
  if (document.getElementById(MARKER)) return;

  var WINDOW_S = 7;
  var rttHist = []; // {t, ms}
  var warnMs = 80;
  var critMs = 200;

  function css() {
    if (document.getElementById(MARKER + "-css")) return;
    var s = document.createElement("style");
    s.id = MARKER + "-css";
    s.textContent =
      "#" + MARKER + "{" +
      "margin-top:10px;display:flex;flex-direction:column;gap:10px}" +
      "#" + MARKER + " .panel{" +
      "padding:8px 10px;border-radius:8px;background:rgba(0,0,0,.28);" +
      "color:#e8e8e8;font:12px/1.35 system-ui,sans-serif}" +
      "#" + MARKER + " .head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px}" +
      "#" + MARKER + " .title{opacity:.85;font-size:11px;letter-spacing:.02em}" +
      "#" + MARKER + " .meta{font:600 12px/1 ui-monospace,monospace}" +
      "#" + MARKER + " .meta.ok{color:#7dcea0}" +
      "#" + MARKER + " .meta.warn{color:#f4d03f}" +
      "#" + MARKER + " .meta.crit{color:#e74c3c}" +
      "#" + MARKER + " .meta.unknown{color:#95a5a6}" +
      "#" + MARKER + " canvas{display:block;width:100%;height:64px;background:rgba(0,0,0,.25);border-radius:4px}";
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
        '<div class="panel" data-kind="latency">' +
        '<div class="head">' +
        '<span class="title">Latency · last 7s</span>' +
        '<span class="meta unknown">—</span>' +
        "</div>" +
        '<canvas width="320" height="64" aria-label="RTT last 7 seconds"></canvas>' +
        "</div>" +
        '<div class="panel" data-kind="battery">' +
        '<div class="head">' +
        '<span class="title">Battery % · last 7s</span>' +
        '<span class="meta unknown">—</span>' +
        "</div>" +
        '<canvas width="320" height="64" aria-label="battery percent last 7 seconds"></canvas>' +
        "</div>";
      root.appendChild(box);
    }
    return box;
  }

  function trimHist(arr, now) {
    var cutoff = now - WINDOW_S * 1000;
    while (arr.length && arr[0].t < cutoff) arr.shift();
  }

  function levelForMs(ms) {
    if (ms == null || !isFinite(ms)) return "unknown";
    if (ms >= critMs) return "crit";
    if (ms >= warnMs) return "warn";
    return "ok";
  }

  function levelForPct(pct, warn, crit) {
    if (pct == null || !isFinite(pct)) return "unknown";
    if (pct <= crit) return "crit";
    if (pct <= warn) return "warn";
    return "ok";
  }

  function drawSeries(canvas, series, level, opts) {
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    var dpr = window.devicePixelRatio || 1;
    var cssW = canvas.clientWidth || 320;
    var cssH = canvas.clientHeight || 64;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var yMin = opts.yMin;
    var yMax = opts.yMax;
    var valueKey = opts.valueKey;

    ctx.strokeStyle = "rgba(255,255,255,.12)";
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75].forEach(function (f) {
      var y = cssH - f * (cssH - 4) - 2;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(cssW, y);
      ctx.stroke();
    });

    if (!series || series.length < 1) {
      ctx.fillStyle = "rgba(255,255,255,.35)";
      ctx.font = "11px system-ui,sans-serif";
      ctx.fillText("waiting for samples…", 8, cssH / 2 + 4);
      return;
    }

    // Auto-scale latency: pad around observed max, keep floor at opts.yMin.
    if (opts.autoScale) {
      var hi = yMin;
      series.forEach(function (pt) {
        var v = Number(pt[valueKey]);
        if (isFinite(v) && v > hi) hi = v;
      });
      yMax = Math.max(yMin + 1, Math.ceil(hi * 1.2));
      if (yMax < opts.floorMax) yMax = opts.floorMax;
    }

    var stroke =
      level === "crit" ? "#e74c3c" : level === "warn" ? "#f4d03f" : "#7dcea0";
    var fill =
      level === "crit"
        ? "rgba(231,76,60,.22)"
        : level === "warn"
          ? "rgba(244,208,63,.2)"
          : "rgba(125,206,160,.2)";

    var now = opts.now != null ? opts.now : Date.now();
    function xAt(tAbs) {
      // absolute ms timestamps → map last WINDOW_S onto width
      var tRel = (tAbs - now) / 1000;
      return ((tRel + WINDOW_S) / WINDOW_S) * cssW;
    }
    function xAtRel(tRel) {
      return ((tRel + WINDOW_S) / WINDOW_S) * cssW;
    }
    function yAt(v) {
      var span = yMax - yMin || 1;
      var p = (Number(v) - yMin) / span;
      p = Math.max(0, Math.min(1, p));
      return cssH - 2 - p * (cssH - 4);
    }

    var useAbs = opts.absTime === true;
    ctx.beginPath();
    series.forEach(function (pt, i) {
      var x = useAbs ? xAt(pt.t) : xAtRel(pt.t);
      var y = yAt(pt[valueKey]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    var last = series[series.length - 1];
    var first = series[0];
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.stroke();

    var xLast = useAbs ? xAt(last.t) : xAtRel(last.t);
    var xFirst = useAbs ? xAt(first.t) : xAtRel(first.t);
    ctx.lineTo(xLast, cssH);
    ctx.lineTo(xFirst, cssH);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }

  function panelEls(kind) {
    var box = document.getElementById(MARKER);
    if (!box) return null;
    var panel = box.querySelector('[data-kind="' + kind + '"]');
    if (!panel) return null;
    return {
      meta: panel.querySelector(".meta"),
      canvas: panel.querySelector("canvas"),
    };
  }

  function paintLatency() {
    var els = panelEls("latency");
    if (!els || !els.meta || !els.canvas) return;
    var now = Date.now();
    trimHist(rttHist, now);
    var last = rttHist.length ? rttHist[rttHist.length - 1] : null;
    var ms = last ? last.ms : null;
    var level = levelForMs(ms);
    els.meta.className = "meta " + level;
    if (ms == null) {
      els.meta.textContent = "—";
      drawSeries(els.canvas, [], "unknown", {
        yMin: 0,
        yMax: 200,
        valueKey: "ms",
        absTime: true,
        now: now,
      });
      return;
    }
    var vid = window.__anxVideoMs;
    els.meta.textContent =
      Math.round(ms) +
      " ms RTT" +
      (vid != null ? " · vid " + Math.round(vid) + " ms" : "");
    drawSeries(els.canvas, rttHist, level, {
      yMin: 0,
      yMax: 200,
      floorMax: 100,
      autoScale: true,
      valueKey: "ms",
      absTime: true,
      now: now,
    });
  }

  function paintBattery(body) {
    var els = panelEls("battery");
    if (!els || !els.meta || !els.canvas) return;

    if (!body || !body.available) {
      els.meta.className = "meta unknown";
      els.meta.textContent = "n/a";
      drawSeries(els.canvas, [], "unknown", {
        yMin: 0,
        yMax: 100,
        valueKey: "pct",
        absTime: false,
      });
      return;
    }

    var level =
      body.level ||
      levelForPct(body.percent, body.warn_pct || 30, body.crit_pct || 15);
    els.meta.className = "meta " + level;
    els.meta.textContent =
      (body.percent != null ? Number(body.percent).toFixed(0) + "%" : "—") +
      (body.volts != null ? " · " + Number(body.volts).toFixed(2) + " V" : "");
    drawSeries(els.canvas, body.series || [], level, {
      yMin: 0,
      yMax: 100,
      valueKey: "pct",
      absTime: false,
    });
  }

  function sampleRtt() {
    var t0 = performance.now();
    return fetch("/health", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function () {
        var ms = Math.round(performance.now() - t0);
        var now = Date.now();
        rttHist.push({ t: now, ms: ms });
        trimHist(rttHist, now);
        paintLatency();
      })
      .catch(function () {
        paintLatency();
      });
  }

  function sampleBattery() {
    return fetch("/anx/power", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(paintBattery)
      .catch(function () {
        paintBattery(null);
      });
  }

  function tick() {
    sampleRtt();
    sampleBattery();
  }

  function boot() {
    var sheet = findHardwareSheet();
    if (!sheet) return false;
    ensure(sheet);
    tick();
    setInterval(tick, 400);
    window.addEventListener("resize", function () {
      paintLatency();
      sampleBattery();
    });
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
