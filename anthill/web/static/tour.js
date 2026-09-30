/* Anthill onboarding tour - a lightweight coachmark walk-through over the real
   dashboard. Auto-runs once for a new user; replayable via anthillTour.start(true).
   No dependencies; degrades to nothing if targets are missing. */
(function () {
  const STEPS = [
    {sel:'a[href="/chat"]',           title:'Chat',          body:'Ask anything. Answers come from your wiki and the smart cache, plus the web when it helps. Anthill decides how deep to go - say "go deeper" to push further.'},
    {sel:'a[href="/tasks"]',          title:'Tasks',         body:'Hand off recurring or scheduled work. Anthill runs it on its own, streams each result, and holds anything consequential for your approval.'},
    {sel:'a[href="/agents"]',         title:'Agents',        body:'A named, persistent worker with a standing mandate - an employee with a role. It works toward its goal on its own, governed like Tasks.'},
    {sel:'a[href="/snippets"]',       title:'Snippets',      body:'Mark any answer worth keeping. It becomes gold knowledge and, once corroborated, is shared with your org.'},
    {sel:'a[href="/memory"]',         title:'Memory',        body:'Anthill remembers durable facts and preferences from your chats and tasks, and recalls them automatically.'},
    {sel:'a[href="/wiki"]',           title:'Wiki',          body:'Your knowledge base, maintained by the model. Saved answers and deep research land here, and chat draws on it automatically.'},
    {sel:'a[href="/skills"]',         title:'Skills',        body:'Package reusable know-how as a Skill the agent loads only when it is relevant.'},
    {sel:'a[href="/teams"]',          title:'Projects',      body:'Spin up a project with its own wiki. Knowledge climbs the ladder: personal, then team, then org.'},
    {sel:'a[href="/connectors/mcp"]', title:'Integrations',  body:'Connect the tools your work lives in - Notion, GitHub, Slack and more - so the agent can use them, each scoped and revocable.'},
    {sel:'a[href="/metrics"]',        title:'Metrics',       body:'See the payoff: money and energy saved, latency, and how much stays inside your perimeter.'},
    {sel:'a[href="/personalize"]',    title:'Make it yours', body:'Tell Anthill your role, tone, and goals so every answer fits how you work. The best first step.', cta:{label:'Personalize now', href:'/personalize'}},
  ];
  let i = 0;
  const els = {};

  function done() { try { fetch('/onboarding/done', {method:'POST'}); } catch (e) {} }

  function clearSpots() {
    document.querySelectorAll('.tour-spot').forEach(el => {
      el.classList.remove('tour-spot');
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

  function finish() { cleanup(); done(); }

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
    el.classList.add('tour-spot');
    el.scrollIntoView({block: 'center', behavior: 'smooth'});
  }

  function showStep(n) {
    clearSpots();
    let step = STEPS[n], target = step && document.querySelector(step.sel);
    while (step && !target) { n++; step = STEPS[n]; target = step && document.querySelector(step.sel); }
    if (!step) { finish(); return; }
    i = n;
    spotlight(target);
    const r = target.getBoundingClientRect(), c = els.card;
    c.querySelector('.tour-title').textContent = step.title;
    c.querySelector('.tour-body').textContent  = step.body;
    c.querySelector('.tour-count').textContent = (n + 1) + ' / ' + STEPS.length;
    const cta = c.querySelector('.tour-cta');
    if (step.cta) { cta.style.display = ''; cta.textContent = step.cta.label; cta.onclick = () => { done(); location.href = step.cta.href; }; }
    else cta.style.display = 'none';
    c.querySelector('.tour-back').disabled = (n === 0);
    c.querySelector('.tour-next').textContent = (n === STEPS.length - 1) ? 'Done' : 'Next';
    c.style.left = Math.min(r.right + 14, window.innerWidth - 320) + 'px';
    c.style.top  = Math.max(12, Math.min(r.top, window.innerHeight - 230)) + 'px';
  }

  function next() { (i >= STEPS.length - 1) ? finish() : showStep(i + 1); }
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
        '<b class="tour-title" style="font-size:15px"></b>' +
        '<span class="tour-count" style="font-size:11px;color:var(--text-muted,#999)"></span>' +
      '</div>' +
      '<p class="tour-body" style="font-size:13px;line-height:1.55;margin:0 0 12px"></p>' +
      '<div style="display:flex;gap:8px;align-items:center">' +
        '<button class="tour-skip" style="border:none;background:none;color:var(--text-muted,#999);cursor:pointer;font-size:12px">Skip</button>' +
        '<span style="flex:1"></span>' +
        '<button class="tour-back" style="border:1px solid var(--border,rgba(0,0,0,.12));background:none;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:13px">Back</button>' +
        '<button class="tour-cta" style="display:none;border:none;background:var(--amber,#2F6B4F);color:#1A110A;border-radius:8px;padding:5px 10px;cursor:pointer;font-weight:600;font-size:13px"></button>' +
        '<button class="tour-next" style="border:none;background:var(--amber,#2F6B4F);color:#1A110A;border-radius:8px;padding:5px 12px;cursor:pointer;font-weight:600;font-size:13px">Next</button>' +
      '</div>';
    document.body.appendChild(card); els.card = card;
    card.querySelector('.tour-skip').onclick = finish;
    card.querySelector('.tour-back').onclick = back;
    card.querySelector('.tour-next').onclick = next;
    document.addEventListener('keydown', onKey);
    showStep(0);
  }

  window.anthillTour = { start };

  // Opt-in only: the 11-step product tour is NOT auto-launched at first login (a brand-new user landed
  // in an 11-step coachmark wizard before they had even set up their account - founder: "that's
  // nonsense"). It stays fully replayable on demand from the sidebar footer's "Take a tour" link
  // (anthillTour.start), alongside each surface's own short tour.
})();
