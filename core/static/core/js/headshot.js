// MyDay Event Tools: search filter on the event and bowler lists, and the Athlete Head Shot camera.
// The photo is cropped to a square on the phone (drag + zoom inside the circle), shrunk to 600 x 600 JPEG
// and uploaded; MyDay passes it to Mission Control for the LED wall banners.
(function () {
  "use strict";

  // --- search box over a list ---------------------------------------------------
  document.querySelectorAll("[data-filter]").forEach(function (box) {
    var list = document.querySelector(box.dataset.filter);
    if (!list) return;
    var none = list.querySelector(".hs-none");
    box.addEventListener("input", function () {
      var words = box.value.toLowerCase().split(/\s+/).filter(Boolean), shown = 0;
      list.querySelectorAll("[data-text]").forEach(function (row) {
        var t = row.dataset.text.toLowerCase();
        var ok = words.every(function (w) { return t.indexOf(w) >= 0; });
        row.hidden = !ok; if (ok) shown++;
      });
      if (none) none.hidden = shown > 0;
    });
  });

  // --- the camera ---------------------------------------------------------------
  var root = document.querySelector("[data-headshot]");
  if (!root) return;
  var file = root.querySelector("[data-hs-file]"), canvas = root.querySelector("[data-hs-canvas]");
  var zoom = root.querySelector("[data-hs-zoom]"), upload = root.querySelector("[data-hs-upload]");
  var current = root.querySelector("[data-hs-current]"), hint = root.querySelector("[data-hs-hint]");
  var form = root.querySelector("[data-hs-form]"), errBox = document.querySelector("[data-hs-error]");
  var label = root.querySelector("[data-hs-snap-label]");
  var g = canvas.getContext("2d"), S = canvas.width;
  var img = null, scale = 1, base = 1, x = 0, y = 0; // x, y = image centre in canvas pixels

  function draw() {
    g.fillStyle = "#0A1017"; g.fillRect(0, 0, S, S);
    if (!img) return;
    var w = img.naturalWidth * base * scale, h = img.naturalHeight * base * scale;
    // keep the circle covered
    x = Math.min(w / 2, Math.max(S - w / 2, x)); y = Math.min(h / 2, Math.max(S - h / 2, y));
    g.drawImage(img, x - w / 2, y - h / 2, w, h);
  }

  file.addEventListener("change", function () {
    var f = file.files && file.files[0];
    if (!f) return;
    var url = URL.createObjectURL(f), im = new Image();
    im.onload = function () {
      img = im; base = S / Math.min(im.naturalWidth, im.naturalHeight); scale = 1; zoom.value = 1;
      x = S / 2; y = S / 2 - Math.max(0, im.naturalHeight * base - S) * 0.2; // faces sit high in a portrait
      canvas.hidden = false; zoom.hidden = false; hint.hidden = false; upload.hidden = false;
      if (current) current.hidden = true;
      label.textContent = "Take it again";
      if (errBox) errBox.hidden = true;
      draw();
    };
    im.onerror = function () { showError("That photo couldn't be opened. Take it again."); };
    im.src = url;
  });

  zoom.addEventListener("input", function () { scale = parseFloat(zoom.value) || 1; draw(); });

  var drag = null;
  function pt(e) { var r = canvas.getBoundingClientRect(); return { x: (e.clientX - r.left) * S / r.width, y: (e.clientY - r.top) * S / r.height }; }
  canvas.addEventListener("pointerdown", function (e) { if (!img) return; drag = { p: pt(e), x: x, y: y }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", function (e) { if (!drag) return; var p = pt(e); x = drag.x + p.x - drag.p.x; y = drag.y + p.y - drag.p.y; draw(); });
  canvas.addEventListener("pointerup", function () { drag = null; });
  canvas.addEventListener("pointercancel", function () { drag = null; });

  function showError(msg) {
    if (!errBox) return alert(msg); // eslint-disable-line no-alert
    errBox.textContent = msg; errBox.hidden = false; errBox.scrollIntoView({ block: "center" });
  }

  upload.addEventListener("click", function () {
    if (!img) return;
    upload.disabled = true; upload.textContent = "Uploading…";
    canvas.toBlob(function (blob) {
      var fd = new FormData();
      fd.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
      fd.append("photo", blob, "headshot.jpg");
      fetch(location.pathname, { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json().catch(function () { return { ok: false, error: "MyDay didn't answer. Try again." }; }); })
        .then(function (j) {
          if (j.ok) { location.href = j.next; return; }
          throw new Error(j.error || "That didn't upload. Try again.");
        })
        .catch(function (err) {
          showError(err.message || "That didn't upload. Try again.");
          upload.disabled = false; upload.textContent = "Upload head shot";
        });
    }, "image/jpeg", 0.86);
  });
})();
