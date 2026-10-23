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
    form.querySelectorAll(".pin-dot").forEach(function (dot, i) {
      dot.classList.toggle("filled", i < value.length);
    });
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
    var max = parseInt(form.dataset.pinLength || "4", 10);
    var k = key.dataset.key;
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
    var map = { Backspace: "back", Escape: "clear" };
    var k = /^[0-9]$/.test(e.key) ? e.key : map[e.key];
    if (!k) return;
    var btn = form.querySelector('[data-key="' + k + '"]');
    if (btn) { e.preventDefault(); btn.click(); }
  });

  document.addEventListener("DOMContentLoaded", function () { initForms(document); });
  document.addEventListener("htmx:load", function (e) { initForms(e.target); });

  // --- Installable app --------------------------------------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
    });
  }
})();
