// Speech-to-text for any input/textarea via the browser Web Speech API.
// anthillDictate(targetId, btn) toggles dictation into the target field. When the
// browser has no speech API, it points the user to Wispr Flow (system-wide voice
// typing that works in any app, including this one).
(function () {
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var active = null;
  var activeBtn = null;

  window.anthillDictate = function (targetId, btn) {
    var el = document.getElementById(targetId);
    if (!el) return;
    if (!SR) {
      alert("In-page dictation isn't supported in this browser.\n\n" +
            "Tip: Wispr Flow (wisprflow.ai) gives fast, system-wide voice typing " +
            "in any app - including this one. Install it, then dictate into any field.");
      return;
    }
    if (active) { active.stop(); return; }     // toggle off

    var rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    var base = el.value;
    rec.onresult = function (e) {
      var txt = "";
      for (var i = e.resultIndex; i < e.results.length; i++) txt += e.results[i][0].transcript;
      el.value = (base ? base.replace(/\s*$/, "") + " " : "") + txt;
      el.dispatchEvent(new Event("input"));
    };
    rec.onend = function () {
      active = null;
      if (activeBtn) activeBtn.classList.remove("dictating");
      activeBtn = null;
    };
    rec.onerror = rec.onend;
    active = rec;
    activeBtn = btn || null;
    if (btn) btn.classList.add("dictating");
    try { rec.start(); el.focus(); } catch (e) { rec.onend(); }
  };
})();
