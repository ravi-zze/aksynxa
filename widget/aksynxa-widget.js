/* AKSynxa Website Chat Widget — single file, no build step, no deps.
 *
 * Embed (ek line):
 *   <script src="https://aksynxa-api.onrender.com/widget/aksynxa-widget.js"
 *           data-brand="demo-dental" defer></script>
 *   (self-host karna ho to file kahin bhi rakho — bas script tag wahi hai)
 *
 * Attributes:
 *   data-brand    (required) kis brand ka bot chalega
 *   data-api-base (optional) API server ka base URL; default = current
 *                 script ka origin guess / https://aksynxa-api.onrender.com
 *   data-title    (optional) chat header me dikhne wala naam
 *
 * Widget POST /simulate par baat karta hai — wahi demo engine jo
 * website demo me chalta hai. Visitor ka ek random id banta hai,
 * isliye har visitor ki apni conversation rehti hai.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;
  var brand = script.getAttribute("data-brand") || "aksynxa";
  var apiBase = (script.getAttribute("data-api-base") || "").replace(/\/$/, "");
  if (!apiBase) {
    try {
      apiBase = new URL(script.src).origin;
    } catch (e) {
      apiBase = "https://aksynxa-api.onrender.com";
    }
  }
  var title = script.getAttribute("data-title") || "Chat with us";

  var visitorId = "web-" + Math.random().toString(36).slice(2, 10);

  var css = [
    "#aksx-bubble{position:fixed;right:20px;bottom:20px;width:58px;height:58px;border-radius:50%;",
    "background:linear-gradient(135deg,#2563eb,#7c3aed);color:#fff;border:none;cursor:pointer;",
    "box-shadow:0 8px 24px rgba(11,31,74,.35);font-size:26px;z-index:99990;display:flex;",
    "align-items:center;justify-content:center}",
    "#aksx-panel{position:fixed;right:20px;bottom:88px;width:340px;max-width:calc(100vw - 32px);",
    "height:460px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;",
    "box-shadow:0 16px 48px rgba(11,31,74,.28);z-index:99991;display:none;flex-direction:column;",
    "overflow:hidden;font-family:'Segoe UI',Arial,sans-serif;border:1px solid #dde3f0}",
    "#aksx-panel.open{display:flex}",
    "#aksx-head{background:#0b1f4a;color:#fff;padding:14px 16px;font-weight:700;font-size:15px;",
    "display:flex;justify-content:space-between;align-items:center}",
    "#aksx-head small{display:block;font-weight:400;opacity:.75;font-size:11px}",
    "#aksx-close{background:none;border:none;color:#fff;font-size:18px;cursor:pointer}",
    "#aksx-msgs{flex:1;overflow-y:auto;padding:12px;background:#f4f6fb}",
    ".aksx-m{max-width:82%;padding:9px 12px;border-radius:14px;margin-bottom:8px;font-size:14px;",
    "line-height:1.45;white-space:pre-wrap;word-wrap:break-word}",
    ".aksx-bot{background:#fff;border:1px solid #dde3f0;border-bottom-left-radius:4px}",
    ".aksx-user{background:linear-gradient(135deg,#2563eb,#7c3aed);color:#fff;margin-left:auto;",
    "border-bottom-right-radius:4px}",
    "#aksx-form{display:flex;gap:8px;padding:10px;border-top:1px solid #eef2f7;background:#fff}",
    "#aksx-input{flex:1;font-size:15px;padding:11px;border:2px solid #dde3f0;border-radius:10px}",
    "#aksx-input:focus{outline:none;border-color:#2563eb}",
    "#aksx-send{background:#2563eb;color:#fff;border:none;border-radius:10px;padding:0 16px;",
    "font-size:15px;font-weight:700;cursor:pointer}",
    "#aksx-send:disabled{opacity:.5}"
  ].join("");

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var bubble = document.createElement("button");
  bubble.id = "aksx-bubble";
  bubble.setAttribute("aria-label", "Chat kholo");
  bubble.textContent = "💬";

  var panel = document.createElement("div");
  panel.id = "aksx-panel";
  panel.innerHTML =
    '<div id="aksx-head"><div>' + title +
    "<small>Powered by AKSynxa</small></div>" +
    '<button id="aksx-close" aria-label="Band karo">×</button></div>' +
    '<div id="aksx-msgs"></div>' +
    '<form id="aksx-form"><input id="aksx-input" type="text" ' +
    'placeholder="Message likho…" autocomplete="off">' +
    '<button id="aksx-send" type="submit">Bhejo</button></form>';

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  var msgs = panel.querySelector("#aksx-msgs");
  var input = panel.querySelector("#aksx-input");
  var sendBtn = panel.querySelector("#aksx-send");
  var started = false;

  function addMsg(text, who) {
    var d = document.createElement("div");
    d.className = "aksx-m " + (who === "user" ? "aksx-user" : "aksx-bot");
    d.textContent = text;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function send(text) {
    sendBtn.disabled = true;
    return fetch(apiBase + "/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from: visitorId, text: text, brand: brand })
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        (j.replies || ["(koi reply nahi aaya)"]).forEach(function (rep) {
          addMsg(rep, "bot");
        });
      })
      .catch(function () {
        addMsg("Network me dikkat hai — thodi der baad try karo.", "bot");
      })
      .then(function () { sendBtn.disabled = false; input.focus(); });
  }

  bubble.addEventListener("click", function () {
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) {
      input.focus();
      if (!started) {
        started = true;
        send("hi");
      }
    }
  });
  panel.querySelector("#aksx-close").addEventListener("click", function () {
    panel.classList.remove("open");
  });
  panel.querySelector("#aksx-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    addMsg(text, "user");
    send(text);
  });
})();
