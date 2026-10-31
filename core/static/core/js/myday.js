// MyDay: PIN keypads, the one-off location check at Log on / Log off, and the service worker.
(function () {
  "use strict";

  // --- Location: asked only when a Log on / Log off PIN sheet opens ------------
  function locate(form) {
    if (form._location) return form._location;
    form._location = new Promise(function (resolve) {
      if (!("geolocation" in navigator)) return resolve();
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          form.querySelector("[data-lat]").value = pos.coords.latitude;
          form.querySelector("[data-lng]").value = pos.coords.longitude;
          resolve();
        },
        function () { resolve(); }, // blocked or failed: the server treats it as off site
        { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
      );
    });
    return form._location;
  }

  function initForms(root) {
    (root || document).querySelectorAll("form[data-locate]").forEach(locate);
  }

  // --- PIN keypad -------------------------------------------------------------
  function render(form) {
    var value = form.querySelector("[data-pin-value]").value;
    var min = parseInt(form.dataset.pinMin || "4", 10);
    var box = form.querySelector("[data-pin-dots]");
    var want = Math.max(min, value.length);
    while (box.children.length < want) { var d = document.createElement("span"); d.className = "pin-dot"; box.appendChild(d); }
    while (box.children.length > want) box.removeChild(box.lastChild);
    Array.prototype.forEach.call(box.children, function (dot, i) { dot.classList.toggle("filled", i < value.length); });
    var enter = form.querySelector('[data-key="enter"]');
    if (enter) enter.disabled = value.length < min;
  }

  function submit(form) {
    form.classList.add("busy");
    var go = function () {
      if (form.hasAttribute("hx-post") && window.htmx) {
        window.htmx.trigger(form, "submit");
      } else {
        form.submit();
      }
    };
    if (form.hasAttribute("data-locate")) locate(form).then(go); else go();
  }

  document.addEventListener("click", function (e) {
    var key = e.target.closest("[data-key]");
    if (!key) return;
    var form = key.closest("[data-pin-form]");
    if (!form || form.classList.contains("busy")) return;
    var input = form.querySelector("[data-pin-value]");
    var min = parseInt(form.dataset.pinMin || "4", 10);
    var max = parseInt(form.dataset.pinMax || "6", 10);
    var k = key.dataset.key;
    if (k === "enter") { if (input.value.length >= min) submit(form); return; }
    if (k === "back") input.value = input.value.slice(0, -1);
    else if (k === "clear") input.value = "";
    else if (input.value.length < max) input.value += k;
    render(form);
    if (input.value.length === max) submit(form);
  });

  // Physical keyboard on the lock screen (handy on a desktop or tablet).
  document.addEventListener("keydown", function (e) {
    var form = document.querySelector(".lock [data-pin-form]");
    if (!form) return;
    var map = { Backspace: "back", Escape: "clear", Enter: "enter" };
    var k = /^[0-9]$/.test(e.key) ? e.key : map[e.key];
    if (!k) return;
    var btn = form.querySelector('[data-key="' + k + '"]');
    if (btn) { e.preventDefault(); btn.click(); }
  });

  document.addEventListener("DOMContentLoaded", function () { initForms(document); });
  document.addEventListener("htmx:load", function (e) { initForms(e.target); });

  // --- Mechanic: beep and buzz when a new lane call comes in -------------------
  var seenCalls = null, audio = null;
  function wakeAudio() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!audio && Ctx) audio = new Ctx();
    if (audio && audio.state === "suspended") audio.resume();
  }
  document.addEventListener("pointerdown", wakeAudio, { passive: true });
  function beep() {
    if (navigator.vibrate) navigator.vibrate([300, 150, 300]);
    if (!audio) return;
    [0, 0.35].forEach(function (at) {
      var osc = audio.createOscillator(), gain = audio.createGain();
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.4, audio.currentTime + at);
      gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + at + 0.3);
      osc.connect(gain).connect(audio.destination);
      osc.start(audio.currentTime + at);
      osc.stop(audio.currentTime + at + 0.3);
    });
  }
  function checkCalls(root) {
    var box = root.id === "lane-calls" ? root : root.querySelector && root.querySelector("#lane-calls");
    if (!box || box.dataset.error) return;
    var ids = (box.dataset.openIds || "").split(",").filter(Boolean);
    if (seenCalls && ids.some(function (id) { return seenCalls.indexOf(id) < 0; })) beep();
    seenCalls = ids;
  }
  document.addEventListener("DOMContentLoaded", function () { checkCalls(document); });
  document.addEventListener("htmx:load", function (e) { checkCalls(e.target); });

  // --- Mechanic: call alerts on this phone (Web Push, see core/webpush.py) ----
  function initAlerts() {
    var box = document.querySelector("[data-call-alerts]");
    if (!box) return;
    var status = box.querySelector("[data-alerts-status]");
    var btnOn = box.querySelector("[data-alerts-on]"), btnTest = box.querySelector("[data-alerts-test]"),
        btnOff = box.querySelector("[data-alerts-off]");
    function show(text, on) {
      status.textContent = text;
      btnOn.hidden = on !== false;
      btnTest.hidden = btnOff.hidden = on !== true;
    }
    function post(url, body) {
      return fetch(url, { method: "POST", credentials: "same-origin", body: JSON.stringify(body || {}),
        headers: { "Content-Type": "application/json", "X-CSRFToken": box.dataset.csrf } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    }
    function keyBytes(b64) {
      var raw = atob((b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
      return Uint8Array.from(raw, function (ch) { return ch.charCodeAt(0); });
    }
    var ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    var installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      show(ios && !installed
        ? "To get call alerts on an iPhone: tap Share, then Add to Home Screen, and open MyDay from there."
        : "This browser can't do call alerts. Use Chrome on Android, or MyDay from the Home Screen on an iPhone.");
      return;
    }
    if (Notification.permission === "denied") {
      show("Notifications are blocked for MyDay. Allow them in the phone's settings, then come back here.");
      return;
    }
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function () { return navigator.serviceWorker.ready; })
      .then(function (reg) {
        function turnOn() {
          show("Turning on...", null);
          Notification.requestPermission().then(function (perm) {
            if (perm !== "granted") throw new Error("denied");
            return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(box.dataset.key) });
          }).then(function (sub) { return post(box.dataset.url, sub.toJSON()); })
            .then(function () { show("Call alerts are on for this phone.", true); })
            .catch(function (e) {
              show(e.message === "denied" ? "MyDay needs permission to send notifications for call alerts."
                                          : "Couldn't turn on call alerts. Try again.", false);
            });
        }
        btnOn.onclick = turnOn;
        btnTest.onclick = function () {
          post(box.dataset.testUrl).then(function (r) {
            show(r.sent ? "Test sent. It should pop up in a few seconds." : "The test didn't go through. Turn alerts off and on again.", true);
          }).catch(function () { show("The test didn't go through. Try again.", true); });
        };
        btnOff.onclick = function () {
          reg.pushManager.getSubscription().then(function (sub) {
            if (!sub) return;
            return post(box.dataset.url, { endpoint: sub.endpoint, off: true }).then(function () { return sub.unsubscribe(); });
          }).then(function () { show("Call alerts are off for this phone.", false); });
        };
        return reg.pushManager.getSubscription().then(function (sub) {
          if (!sub || Notification.permission !== "granted") return show(status.textContent, false);
          // Re-send it each visit, so the server still has it after a reinstall or a key change.
          return post(box.dataset.url, sub.toJSON()).then(function () { show("Call alerts are on for this phone.", true); });
        });
      }).catch(function () { show("Couldn't set up call alerts on this phone.", false); });
  }
  document.addEventListener("DOMContentLoaded", initAlerts);

  // --- Installable app --------------------------------------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
    });
  }
})();
