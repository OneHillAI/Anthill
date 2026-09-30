/* Anthill per-surface walkthrough - a lightweight coachmark walk-through over the
   elements of the CURRENT page (Wiki, Memory, Snippets, Skills). Distinct from the
   global onboarding tour (tour.js), which covers the sidebar once for a new user.
   Call anthillWalkthrough(surfaceKey, steps) once per page; it checks completion via
   /walkthroughs/status and auto-starts if not done. The returned handle's .start()
   replays it on demand (a "Take a tour" link), independent of every other surface's
   completion state. No dependencies; a step whose target is missing is skipped. */
(function () {
  function build(surface, steps) {
    let i = 0;
    const els = {};

    function markDone() {
      try {
        fetch('/walkthroughs/done', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: 'surface=' + encodeURIComponent(surface),
        });
      } catch (e) {}
    }

    function clearSpots() {
      document.querySelectorAll('.wt-spot').forEach(el => {
        el.classList.remove('wt-spot');
        el.style.zIndex = ''; el.style.boxShadow = ''; el.style.borderRadius = '';
        el.style.position = el.dataset._pos || ''; delete el.dataset._pos;
      });
    }

    function cleanup() {
      clearSpots();
      if (els.back) els.back.remove();
      if (els.card) els.card.remove();
      els.back = els.card = null;
      document.removeEventListener('keydown', onKey);
    }

    function finish() { cleanup(); markDone(); }

    function onKey(e) {
      if (e.key === 'Escape') finish();
      else if (e.key === 'Enter') next();
    }

    function spotlight(el) {
      el.dataset._pos = el.style.position || '';
      if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      el.style.zIndex = 10002;
      el.style.boxShadow = '0 0 0 3px var(--amber, #2F6B4F)';
      el.style.borderRadius = '8px';
      el.classList.add('wt-spot');
      el.scrollIntoView({block: 'center', behavior: 'smooth'});
    }

    function showStep(n) {
      clearSpots();
      let step = steps[n], target = step && document.querySelector(step.sel);
      while (step && !target) { n++; step = steps[n]; target = step && document.querySelector(step.sel); }
      if (!step) { finish(); return; }
      i = n;
      spotlight(target);
      const r = target.getBoundingClientRect(), c = els.card;
      c.querySelector('.wt-title').textContent = step.title;
      c.querySelector('.wt-body').textContent = step.body;
      c.querySelector('.wt-count').textContent = (n + 1) + ' / ' + steps.length;
      c.querySelector('.wt-back').disabled = (n === 0);
      c.querySelector('.wt-next').textContent = (n === steps.length - 1) ? 'Done' : 'Next';
      c.style.left = Math.min(r.right + 14, window.innerWidth - 320) + 'px';
      c.style.top = Math.max(12, Math.min(r.top, window.innerHeight - 230)) + 'px';
    }

    function next() { (i >= steps.length - 1) ? finish() : showStep(i + 1); }
    function back() { if (i > 0) showStep(i - 1); }

    function start() {
      if (els.card) return;
      const back0 = document.createElement('div');
      back0.style.cssText = 'position:fixed;inset:0;background:rgba(26,17,10,.55);z-index:10000';
      back0.onclick = finish;
      document.body.appendChild(back0); els.back = back0;

      const card = document.createElement('div');
      card.style.cssText = 'position:fixed;z-index:10003;width:300px;max-width:92vw;background:var(--bg-surface,#fff);color:var(--text-primary,#1A110A);border:1px solid var(--border,rgba(0,0,0,.12));border-radius:12px;box-shadow:0 12px 40px rgba(26,17,10,.3);padding:16px;font-family:var(--font-sans,system-ui)';
      card.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<b class="wt-title" style="font-size:15px"></b>' +
          '<span class="wt-count" style="font-size:11px;color:var(--text-muted,#999)"></span>' +
        '</div>' +
        '<p class="wt-body" style="font-size:13px;line-height:1.55;margin:0 0 12px"></p>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<button class="wt-skip" style="border:none;background:none;color:var(--text-muted,#999);cursor:pointer;font-size:12px">Skip</button>' +
          '<span style="flex:1"></span>' +
          '<button class="wt-back" style="border:1px solid var(--border,rgba(0,0,0,.12));background:none;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:13px">Back</button>' +
          '<button class="wt-next" style="border:none;background:var(--amber,#2F6B4F);color:#1A110A;border-radius:8px;padding:5px 12px;cursor:pointer;font-weight:600;font-size:13px">Next</button>' +
        '</div>';
      document.body.appendChild(card); els.card = card;
      card.querySelector('.wt-skip').onclick = finish;
      card.querySelector('.wt-back').onclick = back;
      card.querySelector('.wt-next').onclick = next;
      document.addEventListener('keydown', onKey);
      // Wait a frame for layout to settle (e.g. right after DOMContentLoaded, before web
      // fonts/late reflows land) so the first spotlight measures real positions, not stale ones.
      requestAnimationFrame(() => requestAnimationFrame(() => showStep(0)));
    }

    return {start};
  }

  // opts.autostart (default true): when false, register the walkthrough as replayable via
  // window._anthillWalkthroughs[surface].start() but do NOT auto-fire on first visit. The per-surface
  // tours pass {autostart:false} now that the hub-level self-explanation leads
  // (docs/specs/knowledge-guidance-reconciliation.md); their content is unchanged, just on-demand.
  window.anthillWalkthrough = function (surface, steps, opts) {
    const w = build(surface, steps);
    window._anthillWalkthroughs = window._anthillWalkthroughs || {};
    window._anthillWalkthroughs[surface] = w;
    if (opts && opts.autostart === false) return w;
    fetch('/walkthroughs/status?surface=' + encodeURIComponent(surface))
      .then(r => r.json())
      .then(s => { if (s && !s.done) w.start(); })
      .catch(() => {});
    return w;
  };
})();
