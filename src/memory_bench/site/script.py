"""The site's script: theme toggle, copy buttons, decider tabs, the row, frontier and audit link, the studio
tabs and the stage stepper.

Plain JS, no framework. Every page reads in full without it; this only moves state between elements the
builder already wrote. It formats nothing and computes no rate.
"""

# Runs in <head> before paint: the stored theme choice (if any) and the js class.
HEAD_JS = ("document.documentElement.className+=' js';"
           "try{var t=localStorage.getItem('hercyna-theme');"
           "if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t)}catch(e){}")

JS = r"""
(function () {
  var d = document, root = d.documentElement;
  var reduce = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  var darkQ = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : { matches: false };
  var live = d.getElementById('live');
  function say(t) { if (live) { live.textContent = ''; setTimeout(function () { live.textContent = t; }, 30); } }
  function all(sel, el) { return [].slice.call((el || d).querySelectorAll(sel)); }

  /* theme: the system setting unless the visitor chose one here */
  var tbtn = d.getElementById('theme');
  function theme() { return root.getAttribute('data-theme') || (darkQ.matches ? 'dark' : 'light'); }
  function syncTheme() {
    if (!tbtn) return;
    var dark = theme() === 'dark';
    tbtn.setAttribute('aria-pressed', dark ? 'true' : 'false');
    var l = tbtn.querySelector('.tl'); if (l) l.textContent = dark ? 'Light' : 'Dark';
    tbtn.setAttribute('aria-label', dark ? 'Switch to the light theme' : 'Switch to the dark theme');
    all('.reel video').forEach(function (v) { if (v.offsetParent === null && !v.paused) v.pause(); });
  }
  if (tbtn) {
    tbtn.addEventListener('click', function () {
      var next = theme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('hercyna-theme', next); } catch (e) {}
      syncTheme(); say(next === 'dark' ? 'Dark theme' : 'Light theme');
    });
    if (darkQ.addEventListener) darkQ.addEventListener('change', syncTheme);
    syncTheme();
  }

  /* copy controls (with terms checkbox gate when data-terms-target is present) */
  d.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button.copy');
    if (!b) return;
    var reqId = b.getAttribute('data-terms-target');
    if (reqId) {
      var cb = d.getElementById(reqId);
      if (cb && !cb.checked) {
        var box = cb.closest('.terms-gate');
        if (box) box.classList.add('terms-warn');
        cb.focus();
        say('Tick I have read the terms before copying the command.');
        return;
      }
    }
    var text = b.getAttribute('data-copy'), l = b.querySelector('.cl'), was = l ? l.textContent : '';
    function done() {
      b.classList.add('done'); if (l) l.textContent = 'Copied'; say('Copied ' + text);
      clearTimeout(b._t);
      b._t = setTimeout(function () { b.classList.remove('done'); if (l) l.textContent = was; }, 1800);
    }
    function fallback() {
      var c = b.parentNode.querySelector('code');
      if (!c) { say('Copy is not available here.'); return; }
      var r = d.createRange(), s = getSelection();
      r.selectNodeContents(c); s.removeAllRanges(); s.addRange(r);
      try { if (d.execCommand('copy')) { s.removeAllRanges(); c.scrollLeft = 0; done(); } else say('Selected. Press copy on your keyboard.'); }
      catch (_) { say('Selected. Press copy on your keyboard.'); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  });

  /* clear terms warning when checkbox is ticked */
  d.addEventListener('change', function (e) {
    if (e.target && e.target.classList && e.target.classList.contains('terms-ack')) {
      var box = e.target.closest('.terms-gate');
      if (box && e.target.checked) box.classList.remove('terms-warn');
    }
  });

  /* updates trigger in header and footer */
  var updBox = d.getElementById('updates');
  if (updBox) {
    all('[data-open-updates]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        updBox.open = true;
        updBox.scrollIntoView({ block: 'start', behavior: 'instant' });
        var target = updBox.querySelector('input[type="email"], summary');
        if (target) target.focus();
        say('Updates panel opened');
      });
    });
    var uform = updBox.querySelector('form.upd-form');
    if (uform) {
      uform.addEventListener('submit', function (e) {
        var hp = uform.querySelector('input.hp-field');
        var consent = uform.querySelector('input[type="checkbox"][required]');
        var st = uform.querySelector('.upd-status');
        if (hp && hp.value.trim()) {
          e.preventDefault();
          if (st) st.textContent = 'Submission blocked.';
          return;
        }
        if (consent && !consent.checked) {
          e.preventDefault();
          consent.focus();
          if (st) st.textContent = 'Tick consent before subscribing.';
          say('Tick consent before subscribing.');
        }
      });
    }
  }

  /* arriving with #section from another page: aim again once fonts and figures have settled the layout */
  if (location.hash.length > 1) {
    var tgt = d.getElementById(decodeURIComponent(location.hash.slice(1)));
    var aim = function () { if (tgt) tgt.scrollIntoView({ block: 'start', behavior: 'instant' }); };
    if (tgt) {
      if (d.fonts && d.fonts.ready) d.fonts.ready.then(function () { requestAnimationFrame(aim); });
      window.addEventListener('load', function () { requestAnimationFrame(aim); });
    }
  }

  /* rail: on a narrow screen the nav scrolls sideways, so keep the current link in view */
  var rnav = d.querySelector('.rail-nav');
  function reveal(a) {
    if (!rnav || !a || rnav.scrollWidth <= rnav.clientWidth + 1) return;
    rnav.scrollLeft = Math.max(0, a.offsetLeft - (rnav.clientWidth - a.offsetWidth) / 2);
  }
  reveal(d.querySelector('.rail-nav a[aria-current]'));

  /* rail: mark the section in view */
  var links = all('.rail-nav a[href^="#"]');
  if (links.length && window.IntersectionObserver) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { var on = a.getAttribute('href') === '#' + en.target.id; a.classList.toggle('on', on); if (on) reveal(a); });
      });
    }, { rootMargin: '-30% 0px -60% 0px' });
    links.forEach(function (a) { var s = d.getElementById(a.getAttribute('href').slice(1)); if (s) io.observe(s); });
  }

  /* board: decider tabs, baselines, and the row / frontier / audit link */
  var board = d.getElementById('board');
  if (board) {
    var tabs = all('.tab', board), panels = all('.dpanel', board), base = d.getElementById('f-base');
    panels.forEach(function (p, i) {
      p.setAttribute('role', 'tabpanel');
      if (tabs[i]) p.setAttribute('aria-labelledby', tabs[i].id);
      var pinned = null;
      function show(r, announce) {
        all('.row', p).forEach(function (x) { var on = x.getAttribute('data-r') === r; x.classList.toggle('active', on);
          var s = x.querySelector('.sys'); if (s) s.setAttribute('aria-pressed', on ? 'true' : 'false'); });
        all('.pt', p).forEach(function (x) { x.classList.toggle('active', x.getAttribute('data-r') === r); });
        all('.audit', p).forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-r') === r); });
        if (announce) { var a = p.querySelector('.audit.on b'); if (a) say(a.textContent + ' selected'); }
      }
      function pin(r) { pinned = r; show(r, true); }
      p._pin = pin;
      all('.row', p).forEach(function (x) {
        var r = x.getAttribute('data-r'), s = x.querySelector('.sys');
        if (s) { s.setAttribute('role', 'button'); s.tabIndex = 0; }
        x.addEventListener('mouseenter', function () { show(r); });
        x.addEventListener('click', function () { pin(r); });
        if (s) s.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pin(r); } });
      });
      all('.pt', p).forEach(function (x) {
        var r = x.getAttribute('data-r');
        x.addEventListener('mouseenter', function () { show(r); });
        x.addEventListener('click', function () { pin(r); });
        x.addEventListener('focus', function () { show(r); });
        x.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pin(r); } });
      });
      [p.querySelector('tbody'), p.querySelector('.frontier')].forEach(function (z) {
        if (z) z.addEventListener('mouseleave', function () { if (pinned !== null) show(pinned); });
      });
      var first = p.querySelector('.row:not(.off)');
      pinned = first ? first.getAttribute('data-r') : null;
      if (pinned !== null) show(pinned);
    });
    function filter() {
      var keep = !base || base.checked;
      panels.forEach(function (p) {
        all('.row, .pt', p).forEach(function (x) { x.classList.toggle('off', !keep && x.getAttribute('data-base') === '1'); });
        var none = p.querySelector('.none'); if (none) none.hidden = !!p.querySelector('.row:not(.off)');
        var act = p.querySelector('.row.active');
        if ((!act || act.classList.contains('off')) && p._pin) {
          var f = p.querySelector('.row:not(.off)'); if (f) p._pin(f.getAttribute('data-r'));
        }
      });
    }
    if (base) base.addEventListener('change', function () { filter(); say(base.checked ? 'Baselines shown' : 'Baselines hidden'); });
    function pick(i, focus) {
      tabs.forEach(function (t, j) { t.setAttribute('aria-selected', j === i ? 'true' : 'false'); t.tabIndex = j === i ? 0 : -1; });
      panels.forEach(function (p, j) { p.hidden = j !== i; });
      if (focus && tabs[i]) tabs[i].focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener('click', function () { pick(i); });
      t.addEventListener('keydown', function (e) {
        var k = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (k) { e.preventDefault(); pick((i + k + tabs.length) % tabs.length, true); }
      });
    });
    if (tabs.length) pick(0);
    filter();
  }

  /* studio: tabs, and the stage stepper that advances one stage at a time */
  var studio = d.getElementById('studio');
  if (studio) {
    var stabs = all('[role="tab"]', studio), panes = all('.studio-stage', studio);
    var flow = studio.querySelector('.flow'), stages = all('.stage', studio), cap = null;
    var play = d.getElementById('st-play'), next = d.getElementById('st-next');
    var cur = 0, timer = null, playing = !reduce.matches, seen = false;
    function setStage(i, announce) {
      cur = i;
      if (flow) flow.setAttribute('data-s', String(i));
      stages.forEach(function (s, j) { s.classList.toggle('on', j === i); s.setAttribute('aria-pressed', j === i ? 'true' : 'false'); });
      if (cap && stages[i]) cap.textContent = stages[i].getAttribute('data-cap');
      if (announce && stages[i]) say(stages[i].getAttribute('data-cap'));
    }
    function tick() { setStage((cur + 1) % stages.length); }
    function run() {
      clearInterval(timer); timer = null;
      var stepOn = !panes[0] || !panes[0].hidden;
      if (playing && seen && stepOn && !reduce.matches) timer = setInterval(tick, 3400);
      if (play) { play.textContent = playing ? 'Pause' : 'Play'; play.setAttribute('aria-pressed', playing ? 'false' : 'true'); }
    }
    stages.forEach(function (s, j) {
      s.tabIndex = 0; s.setAttribute('role', 'button');
      s.addEventListener('click', function () { playing = false; setStage(j, true); run(); });
      s.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); playing = false; setStage(j, true); run(); } });
    });
    if (play) play.addEventListener('click', function () { playing = !playing; run(); });
    if (next) next.addEventListener('click', function () { playing = false; tick(); say(stages[cur].getAttribute('data-cap')); run(); });
    stabs.forEach(function (t, i) {
      t.addEventListener('click', function () {
        stabs.forEach(function (x, j) { x.setAttribute('aria-selected', j === i ? 'true' : 'false'); });
        panes.forEach(function (p, j) { p.hidden = j !== i; });
        if (i === 0) all('.reel video', studio).forEach(function (v) { if (!v.paused) v.pause(); });
        if (play) play.hidden = i !== 0;
        if (next) next.hidden = i !== 0;
        run();
      });
    });
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (es) { seen = es[0].isIntersecting; run(); }, { threshold: .25 }).observe(studio);
    } else { seen = true; }
    if (reduce.addEventListener) reduce.addEventListener('change', function () { if (reduce.matches) playing = false; run(); });
    setStage(0); run();
  }

  /* v2 exploratory tab groups (chat_agent ranked pairs, 40-cell board, Chow, ACBI) */
  all('[data-tab-group]').forEach(function (grp) {
    var gtabs = all('.v2-tab', grp), gpanels = all('.v2-dpanel', grp);
    var gbase = grp.querySelector('[data-base-toggle]');
    var gsel = grp.querySelector('.decider-select');
    function gpick(i, focus) {
      gtabs.forEach(function (t, j) {
        t.setAttribute('aria-selected', j === i ? 'true' : 'false');
        t.tabIndex = j === i ? 0 : -1;
      });
      gpanels.forEach(function (p, j) { p.setAttribute('data-ready', '1'); p.hidden = j !== i; });
      if (gsel && gsel.selectedIndex !== i) gsel.selectedIndex = i;
      if (focus && gtabs[i]) gtabs[i].focus();
    }
    function gfilter() {
      if (!gbase) return;
      var keep = gbase.checked;
      gpanels.forEach(function (p) {
        all('tr[data-base="1"]', p).forEach(function (r) { r.classList.toggle('off', !keep); });
      });
    }
    gtabs.forEach(function (t, i) {
      t.addEventListener('click', function () { gpick(i, false); });
      t.addEventListener('keydown', function (e) {
        var k = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (k) { e.preventDefault(); gpick((i + k + gtabs.length) % gtabs.length, true); }
      });
    });
    if (gsel) gsel.addEventListener('change', function () {
      var idx = gsel.selectedIndex;
      if (idx >= 0 && idx < gtabs.length) {
        gpick(idx, false);
        say('Filtered to decider ' + gsel.value);
      }
    });
    if (gbase) gbase.addEventListener('change', function () {
      gfilter();
      say(gbase.checked ? 'Baselines shown' : 'Baselines hidden');
    });
    if (gtabs.length) gpick(0, false);
    gfilter();
  });

  /* sortable v2 tables (move parent row and its companion split row cleanly) */
  all('[data-sortable]').forEach(function (tbl) {
    var tbody = tbl.querySelector('tbody');
    if (!tbody) return;
    var sortKey = null, sortDir = 'none';
    var headers = all('th .sort-btn', tbl);
    var group = tbl.closest('[data-tab-group]') || tbl;
    var minis = all('.btn-mini[data-sort-col]', group);
    function valOf(tr, key) {
      if (key === 'orig') return parseFloat(tr.getAttribute('data-orig') || '0');
      var raw = tr.getAttribute('data-' + key);
      var num = parseFloat(raw);
      return isNaN(num) ? (sortDir === 'asc' ? 1e9 : -1e9) : num;
    }
    function applySort(key, dir) {
      sortKey = key; sortDir = dir;
      var rows = all('tr[data-row-id]', tbody);
      var splits = {};
      all('tr[data-parent-row]', tbody).forEach(function (sp) { splits[sp.getAttribute('data-parent-row')] = sp; });
      rows.sort(function (a, b) {
        var va = valOf(a, dir === 'none' ? 'orig' : key);
        var vb = valOf(b, dir === 'none' ? 'orig' : key);
        if (va !== vb) return dir === 'desc' ? vb - va : va - vb;
        return parseFloat(a.getAttribute('data-orig') || '0') - parseFloat(b.getAttribute('data-orig') || '0');
      });
      rows.forEach(function (r) {
        tbody.appendChild(r);
        var sp = splits[r.getAttribute('data-row-id')];
        if (sp) tbody.appendChild(sp);
      });
      headers.forEach(function (btn) {
        var th = btn.closest('th'), col = btn.getAttribute('data-sort-col'), ind = btn.querySelector('.sort-ind');
        var active = (dir !== 'none' && col === key);
        if (th) th.setAttribute('aria-sort', active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
        if (ind) ind.textContent = active ? (dir === 'asc' ? '↑' : '↓') : '↕';
      });
      minis.forEach(function (btn) {
        var col = btn.getAttribute('data-sort-col');
        btn.setAttribute('aria-pressed', (dir !== 'none' && col === key) ? 'true' : 'false');
      });
    }
    function cycle(col, defDir) {
      if (sortKey !== col || sortDir === 'none') applySort(col, defDir || 'asc');
      else if (sortDir === (defDir || 'asc')) applySort(col, (defDir || 'asc') === 'asc' ? 'desc' : 'asc');
      else applySort(col, 'none');
      say(sortDir === 'none' ? 'Order reset' : 'Sorted by ' + col + ' ' + sortDir);
    }
    headers.forEach(function (btn) {
      btn.addEventListener('click', function () {
        cycle(btn.getAttribute('data-sort-col'), btn.getAttribute('data-default-dir') || 'asc');
      });
    });
    minis.forEach(function (btn) {
      btn.addEventListener('click', function () {
        cycle(btn.getAttribute('data-sort-col'), btn.getAttribute('data-default-dir') || 'asc');
      });
    });
  });
})();
"""
