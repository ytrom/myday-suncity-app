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

  // --- Installable app --------------------------------------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
    });
  }
})();
