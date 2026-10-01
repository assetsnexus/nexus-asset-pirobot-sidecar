/* anx-wss-cert-dialog — closable help when control WSS fails (self-signed TLS).
 * Must load before the stock app creates WebSocket(:8888).
 */
(function () {
  var MARKER = "anx-wss-cert-dialog";
  if (window.__anxWssCertHooked) return;
  window.__anxWssCertHooked = true;

  var dismissed = false;
  var openedCertTab = false;
  try {
    dismissed = sessionStorage.getItem(MARKER + "-dismiss") === "1";
    openedCertTab = sessionStorage.getItem(MARKER + "-opened") === "1";
  } catch (e) {}

  var pendingUrl = null;
  var showTimer = null;
  var failCount = 0;

  function wsPort() {
    return String(window.__anxWsPort || 8888);
  }

  function isControlWs(url) {
    if (!url || typeof url !== "string") return false;
    if (url.indexOf("wss:") !== 0 && url.indexOf("ws:") !== 0) return false;
    return url.indexOf(":" + wsPort()) !== -1 || /:8888\b/.test(url);
  }

  function httpsAcceptUrl(wsUrl) {
    try {
      var u = new URL(wsUrl);
      var host = u.hostname || location.hostname;
      var port = u.port || wsPort();
      return "https://" + host + ":" + port + "/?anx_cert=1";
    } catch (e) {
      return "https://" + location.hostname + ":" + wsPort() + "/?anx_cert=1";
    }
  }

  function css() {
    if (document.getElementById(MARKER + "-css")) return;
    var s = document.createElement("style");
    s.id = MARKER + "-css";
    s.textContent =
      "#" + MARKER + "-backdrop{" +
      "position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.55);" +
      "display:flex;align-items:center;justify-content:center;padding:16px}" +
      "#" + MARKER + "{" +
      "max-width:460px;width:100%;background:#1a1d21;color:#e8e8e8;" +
      "border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.45);" +
      "font:14px/1.45 system-ui,sans-serif;padding:18px 20px 16px}" +
      "#" + MARKER + " h2{margin:0 0 10px;font-size:17px;font-weight:650}" +
      "#" + MARKER + " p{margin:0 0 10px;opacity:.92}" +
      "#" + MARKER + " ol{margin:0 0 14px;padding-left:1.25em}" +
      "#" + MARKER + " li{margin:4px 0}" +
      "#" + MARKER + " code{font:12px/1.3 ui-monospace,monospace;background:rgba(255,255,255,.08);" +
      "padding:1px 5px;border-radius:4px;word-break:break-all}" +
      "#" + MARKER + " .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}" +
      "#" + MARKER + " button{" +
      "cursor:pointer;border:0;border-radius:6px;padding:9px 14px;font:600 13px/1.2 system-ui,sans-serif}" +
      "#" + MARKER + " .primary{background:#3d8bfd;color:#fff}" +
      "#" + MARKER + " .primary:hover{background:#5a9dff}" +
      "#" + MARKER + " .ghost{background:rgba(255,255,255,.1);color:#e8e8e8}" +
      "#" + MARKER + " .ghost:hover{background:rgba(255,255,255,.16)}" +
      "#" + MARKER + " .note{opacity:.7;font-size:12px;margin-top:12px;margin-bottom:0}";
    document.head.appendChild(s);
  }

  function hide() {
    var backdrop = document.getElementById(MARKER + "-backdrop");
    if (backdrop) backdrop.remove();
  }

  function dismiss() {
    dismissed = true;
    try {
      sessionStorage.setItem(MARKER + "-dismiss", "1");
    } catch (e) {}
    hide();
  }

  function show(wsUrl) {
    if (dismissed) return;
    if (document.getElementById(MARKER + "-backdrop")) return;
    if (!document.body) {
      pendingUrl = wsUrl;
      document.addEventListener("DOMContentLoaded", function () {
        show(pendingUrl || wsUrl);
      });
      return;
    }
    css();
    var accept = httpsAcceptUrl(wsUrl);
    var postCert = openedCertTab
      ? "<p>If you already accepted the certificate on :" +
        wsPort() +
        ", reload this page. You should see “Certificate accepted” (not a WebSocket error) on that tab.</p>"
      : "";
    var backdrop = document.createElement("div");
    backdrop.id = MARKER + "-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");
    backdrop.setAttribute("aria-labelledby", MARKER + "-title");
    backdrop.innerHTML =
      '<div id="' + MARKER + '">' +
      '<h2 id="' + MARKER + '-title">Control WebSocket blocked</h2>' +
      "<p><code>" +
      (wsUrl || "wss://…:" + wsPort()) +
      "</code> could not connect. On this lab Pi the control port uses a <strong>self-signed</strong> TLS certificate (separate from the UI on :" +
      (location.port || "443") +
      ").</p>" +
      postCert +
      "<p><strong>Dev setup — accept the certificate once:</strong></p>" +
      "<ol>" +
      "<li>Click <strong>Open cert page</strong> → <code>https://…:" +
      wsPort() +
      "/</code>.</li>" +
      "<li>Choose <em>Advanced</em> → <em>Proceed</em> / <em>Accept the risk</em>.</li>" +
      "<li>You must see a page titled <em>Certificate accepted</em>. Then close that tab.</li>" +
      "<li>Click <strong>Reload page</strong> here (do not only refresh blindly if the dialog returns — the cert tab must succeed first).</li>" +
      "</ol>" +
      '<div class="actions">' +
      '<button type="button" class="primary" data-act="open">Open cert page</button>' +
      '<button type="button" class="ghost" data-act="reload">Reload page</button>' +
      '<button type="button" class="ghost" data-act="close">Close</button>' +
      "</div>" +
      '<p class="note">Stock login after WSS connects: admin / 123456</p>' +
      "</div>";

    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) dismiss();
    });
    backdrop.querySelector('[data-act="close"]').addEventListener("click", dismiss);
    backdrop.querySelector('[data-act="reload"]').addEventListener("click", function () {
      location.reload();
    });
    backdrop.querySelector('[data-act="open"]').addEventListener("click", function () {
      openedCertTab = true;
      try {
        sessionStorage.setItem(MARKER + "-opened", "1");
      } catch (e) {}
      window.open(accept, "_blank", "noopener,noreferrer");
    });
    document.body.appendChild(backdrop);
  }

  function scheduleShow(wsUrl) {
    if (dismissed) return;
    failCount += 1;
    // Avoid dialog spam while the stock UI reconnect-loops; wait for a few fails.
    if (failCount < 2) return;
    pendingUrl = wsUrl;
    if (showTimer) clearTimeout(showTimer);
    showTimer = setTimeout(function () {
      show(pendingUrl);
    }, 900);
  }

  function onWsOk() {
    failCount = 0;
    if (showTimer) {
      clearTimeout(showTimer);
      showTimer = null;
    }
    hide();
    try {
      sessionStorage.removeItem(MARKER + "-dismiss");
      sessionStorage.removeItem(MARKER + "-opened");
    } catch (e) {}
    dismissed = false;
    openedCertTab = false;
  }

  var Orig = window.WebSocket;
  function Wrapped(url, protocols) {
    var ws =
      protocols === undefined ? new Orig(url) : new Orig(url, protocols);
    if (!isControlWs(String(url))) return ws;
    var opened = false;
    ws.addEventListener("open", function () {
      opened = true;
      onWsOk();
    });
    ws.addEventListener("error", function () {
      if (!opened) scheduleShow(String(url));
    });
    ws.addEventListener("close", function () {
      if (!opened) scheduleShow(String(url));
    });
    return ws;
  }
  Wrapped.prototype = Orig.prototype;
  Wrapped.CONNECTING = Orig.CONNECTING;
  Wrapped.OPEN = Orig.OPEN;
  Wrapped.CLOSING = Orig.CLOSING;
  Wrapped.CLOSED = Orig.CLOSED;
  window.WebSocket = Wrapped;
})();
