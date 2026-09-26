// appliedin.dev home: plays the hero's application run once, and on request.
(function () {
  "use strict";
  var ticket = document.getElementById("ticket");
  var topbar = document.querySelector(".topbar");
  if (topbar) {
    var onScroll = function () { topbar.classList.toggle("scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
  // Odometer: each digit is a reel of 0-9 twice over, rolled into place once
  // when it scrolls into view.
  Array.prototype.forEach.call(document.querySelectorAll(".odo[data-to]"), function (el) {
    var text = Number(el.dataset.to).toLocaleString("en-US");
    el.setAttribute("aria-label", text);
    el.innerHTML = text.split("").map(function (ch, i) {
      if (!/\d/.test(ch)) return '<span aria-hidden="true">' + ch + "</span>";
      var reel = "";
      for (var k = 0; k < 20; k++) reel += "<span>" + (k % 10) + "</span>";
      return '<span class="d" aria-hidden="true"><span class="dw">' + ch + '</span><span class="r" style="--to:' +
        (-1.3 * (10 + Number(ch))) + "em;--dl:" + (i * 0.05) + 's">' + reel + "</span></span>";
    }).join("");
    var roll = function () { el.classList.add("rolling"); };
    if (!("IntersectionObserver" in window)) return roll();
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { roll(); io.disconnect(); }
    }, { threshold: 0, rootMargin: "0px 0px -10% 0px" });
    io.observe(el);
  });

  if (!ticket) return;
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var timer = null;
  function play() {
    clearTimeout(timer);
    ticket.classList.remove("play", "finished");
    void ticket.offsetWidth; // restart the CSS animations
    if (still) { ticket.classList.add("finished"); return; }
    ticket.classList.add("play");
    timer = setTimeout(function () { ticket.classList.add("finished"); }, 7600);
  }
  var replay = ticket.querySelector(".replay");
  if (replay) replay.addEventListener("click", play);
  play();
})();
