// The operator Station, and a phone's Live tab: one script for both
// documents (`<body data-page="station">` and `data-page="live"`).
//
// It is the prototype's script (desk9, 2026-09-29) with its fake farm taken
// out: every number, phone and account comes from the server's state
// (`#gf-state` on the first paint, `/station/state` after it), and every
// press goes to the farm's own doors. Nothing a server sends is ever parsed
// as markup: the DOM is built with `h()`, the static icons are cloned from
// `<template id="gf-icons">`, and every string goes in through textContent.
(function(){
  'use strict';

  var PAGE = document.body.getAttribute('data-page') || '';
  var HEADER = PAGE === 'live' ? 'live' : 'page';
  var MIN = 60000, TICK_MS = 1500, BEAT_MS = 15000;
  var reduced = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  var WORD = {done: 'Done', decline: 'Decline', or: 'OR', auth: 'Auth', failed: 'Failed'};
  var VERDICTS = ['done', 'decline', 'or', 'auth', 'failed'];
  var VICON = {done: 'tick', decline: 'cross', or: 'ban', auth: 'lock', failed: 'alert'};
  // What a refusal is called, so an answer that is not ok reads red.
  var NO_WORDS = {no: 1, refused: 1, off: 1, stale: 1, origin: 1, password: 1,
    'signed-out': 1, down: 1, broke: 1, none: 1, locked: 1, bad: 1, ask: 1};
  var COPIED = {gmail: 'Gmail', pw: 'password', code: 'code', acct: 'account',
    apw: 'account password', acode: 'account code'};
  var REV = (function(){
    var m = document.querySelector('meta[name="gf-rev"]');
    return m ? m.getAttribute('content') || '' : '';
  })();

  function $(id){ return document.getElementById(id); }
  function W(l){ return l === 'gpt' ? 'GPT' : l === 'spotify' ? 'Spotify' : 'Other'; }
  // Where a phone goes when it is given back: its lane's shelf, or the farm for Other.
  function home(l){ return l === 'other' ? 'the farm' : 'the ' + W(l) + ' shelf'; }
  function laneOf(l){ return l === 'spotify' || l === 'other' ? l : 'gpt'; }
  function num(v, d){ return typeof v === 'number' && isFinite(v) ? v : d; }

  // ------------------------------------------------------------ the DOM
  function h(tag, attrs, ...kids){
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function(k){
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = String(v);
      else if (k === 'hidden' || k === 'disabled') el[k] = true;
      else if (k === 'value') { el.value = String(v); el.setAttribute('value', String(v)); }
      else el.setAttribute(k, v === true ? '' : String(v));
    });
    kids.forEach(function(kid){
      if (kid === null || kid === undefined || kid === false) return;
      el.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return el;
  }
  function icon(name){
    return document.getElementById('gf-icons').content
      .querySelector('[data-i="' + name + '"]').cloneNode(true);
  }
  function fill(el, ...kids){
    el.textContent = '';
    kids.forEach(function(kid){
      if (kid === null || kid === undefined || kid === false) return;
      el.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return el;
  }
  function bump(el){ if (!el) return; el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); }
  function swapIcon(stepEl, name){
    var cp = stepEl.querySelector('.cp');
    if (cp && cp.getAttribute('data-ic') !== name){ cp.setAttribute('data-ic', name); fill(cp, icon(name)); }
  }
  // A new code rises into place, so a fresh one is seen arriving.
  function setCode(el, c){
    if (!el || el.textContent === c) return;
    var was = el.textContent; el.textContent = c;
    if (was){ el.classList.remove('roll'); void el.offsetWidth; el.classList.add('roll'); }
  }
  function scrollTo(el){
    try { el.scrollIntoView({behavior: reduced ? 'auto' : 'smooth', block: 'nearest'}); } catch (e) { /* an old browser: no scroll */ }
  }
  function focusQuiet(el){
    if (!el) return;
    try { el.focus({preventScroll: true}); } catch (e) { el.focus(); }
  }
  function readState(){
    try { return JSON.parse($('gf-state').textContent || 'null'); } catch (e) { report(e, 'first paint'); return null; }
  }

  // ------------------------------------------------------------ toasts
  // What just happened, as one quiet line. Its dot wears the colour of what it
  // is about - the phone's lane or the verdict - and red when it could not be done.
  var TONE = {gpt: 'var(--gpt)', spotify: 'var(--spot)', other: 'var(--other)', done: 'var(--green)',
    decline: 'var(--amber)', or: 'var(--red)', auth: 'var(--cyan)', failed: 'var(--rose)', ok: 'var(--green)'};
  var toastT = null, toastUntil = 0, noteQ = [], noteT = null;
  function say(text, tone){
    var el = $('said');
    if (!el || !text) return;
    var no = tone === true, dur = Math.max(2800, String(text).length * 48);
    el.textContent = text; el.classList.toggle('no', no);
    el.style.setProperty('--tc', no ? 'var(--red)' : TONE[tone] || 'var(--blue)');
    el.classList.add('up');
    toastUntil = Date.now() + dur;
    clearTimeout(toastT); toastT = setTimeout(function(){ el.classList.remove('up'); }, dur);
    if (noteQ.length) runNotes();
  }
  // Notes the farm sends are shown one after another, each for its own time;
  // a press's own toast is shown at once and pushes them back.
  function sayLater(text, tone){ noteQ.push([text, tone]); runNotes(); }
  function runNotes(){
    if (noteT || !noteQ.length) return;
    noteT = setTimeout(function(){
      noteT = null;
      if (Date.now() < toastUntil){ runNotes(); return; }
      var n = noteQ.shift();
      if (n) say(n[0], n[1]);
    }, Math.max(0, toastUntil - Date.now()));
  }

  // ---------------------------------------------------------- plumbing
  var skew = 0;
  function now(){ return Date.now() + skew; }
  function csrf(){ var el = $('gf-csrf'); return el ? el.value || el.getAttribute('value') || '' : ''; }
  // Eight base64url characters, minted once a press and reused on its retry.
  function press(){
    var b = new Uint8Array(6), s = '';
    crypto.getRandomValues(b);
    for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_');
  }
  function wait(ms){ return new Promise(function(done){ setTimeout(done, ms); }); }
  var BROKE = 'Something broke - it is in the server log.';
  // What readJSON answers when the body was given up on (api's timer): the
  // farm did not answer, which api says in its own words - never 'broke'.
  var LOST = 'lost';
  var toldBroke = false;
  function readJSON(r){
    var type = (r && r.headers && r.headers.get && r.headers.get('Content-Type')) || '';
    if (String(type).indexOf('json') < 0){
      if (!toldBroke){ toldBroke = true; report(new Error('a Station answer was not JSON: ' + (r ? r.status : '?') + ' ' + type), 'api'); }
      return Promise.resolve({ok: false, said: 'broke', note: BROKE});
    }
    return r.json().then(function(a){
      if (!a || typeof a !== 'object') return {ok: false, said: 'broke', note: BROKE};
      if (typeof a.go === 'string') location.assign(a.go);
      return a;
    }, function(err){
      if (err && err.name === 'AbortError') return {ok: false, said: LOST, note: ''};
      if (!toldBroke){ toldBroke = true; report(err, 'api'); }
      return {ok: false, said: 'broke', note: BROKE};
    });
  }
  // The one door to the farm. JSON in, JSON out; a network error is tried
  // once more with the same press, except on the profile, where a lost answer
  // may have changed the password already. An answer that has not come in
  // TIMEOUT_MS is given up on as a network error, so one hung press can never
  // hold back the pulls for good.
  var TIMEOUT_MS = 20000;
  function api(path, fields, opts){
    opts = opts || {};
    var method = opts.method || 'POST', headers = {'X-GF-Station': HEADER};
    if (opts.headers) Object.keys(opts.headers).forEach(function(k){ headers[k] = opts.headers[k]; });
    var init = {method: method, headers: headers, credentials: 'same-origin', cache: 'no-store'};
    if (opts.redirect) init.redirect = opts.redirect;
    if (opts.keepalive) init.keepalive = true;
    if (method !== 'GET'){
      var body = new URLSearchParams();
      body.set('csrf', csrf());
      if (!opts.noPress) body.set('press', press());
      if (fields) Object.keys(fields).forEach(function(k){
        body.set(k, fields[k] === null || fields[k] === undefined ? '' : String(fields[k]));
      });
      init.body = body;
    }
    var mine = path.indexOf('/station/me/') === 0;
    function go(again){
      // A fresh controller each try: an aborted signal cannot be used again.
      var ac = window.AbortController ? new window.AbortController() : null, t = null;
      if (ac){
        init.signal = ac.signal;
        t = setTimeout(function(){ ac.abort(); }, opts.timeout || TIMEOUT_MS);
      }
      // No answer - a network error, or headers or a body that never came.
      function lost(){
        if (opts.raw) return null;
        if (mine) return {ok: false, said: 'down', note: 'The farm did not answer - reload to see whether it changed.'};
        if (again) return wait(1500).then(function(){ return go(false); });
        return {ok: false, said: 'down', note: 'The farm did not answer - press it again.'};
      }
      return fetch(path, init).then(function(r){
        // A raw answer's body is read by the caller: the timer still guards it.
        if (opts.raw) return r;
        return readJSON(r).then(function(a){ clearTimeout(t); return a && a.said === LOST ? lost() : a; });
      }, function(){ clearTimeout(t); return lost(); });
    }
    return go(method !== 'GET' && !opts.raw);
  }
  // A throw in this script, into the farm's log. At most five a page.
  var told = 0;
  function report(err, where){
    if (told >= 5) return;
    told++;
    try {
      api('/clienterror', {
        message: String(err && err.message ? err.message : err).slice(0, 500),
        stack: String(err && err.stack ? err.stack : '').slice(0, 2000),
        where: location.pathname + (where ? ' ' + where : ''),
        rev: REV}, {raw: true, noPress: true});
    } catch (e) { /* the report itself failed: nothing more can be said */ }
  }
  addEventListener('error', function(e){
    report(e && e.error ? e.error : (e && e.message) || 'script error', 'onerror');
  });
  addEventListener('unhandledrejection', function(e){ report(e ? e.reason : 'rejection', 'promise'); });

  // --------------------------------------------------------- the code
  function b32(s){
    s = String(s).replace(/[^A-Za-z2-7]/g, '').toUpperCase();
    var A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567', bits = '', out = [];
    for (var i = 0; i < s.length; i++) bits += A.indexOf(s[i]).toString(2).padStart(5, '0');
    for (var j = 0; j + 8 <= bits.length; j += 8) out.push(parseInt(bits.slice(j, j + 8), 2));
    return new Uint8Array(out);
  }
  var keyOf = {}, codes = {};
  // The six digits for a base32 secret at a 30-second step, as '123 456'.
  function totp(secret, step){
    if (!window.crypto || !crypto.subtle) return Promise.reject(new Error('no crypto.subtle'));
    var kp = keyOf[secret] || (keyOf[secret] = crypto.subtle.importKey(
      'raw', b32(secret), {name: 'HMAC', hash: 'SHA-1'}, false, ['sign']));
    return kp.then(function(key){
      var msg = new Uint8Array(8), t = step;
      for (var i = 7; i >= 0; i--){ msg[i] = t & 255; t = Math.floor(t / 256); }
      return crypto.subtle.sign('HMAC', key, msg);
    }).then(function(sig){
      var hh = new Uint8Array(sig), o = hh[hh.length - 1] & 15;
      var c = ((hh[o] & 127) << 24 | hh[o + 1] << 16 | hh[o + 2] << 8 | hh[o + 3]) % 1000000;
      var s = String(c).padStart(6, '0');
      return s.slice(0, 3) + ' ' + s.slice(3);
    });
  }
  // The code as it stands now, or '' while it is worked out; `then` runs when
  // a new one is ready. Without crypto.subtle it is '------' and never rolls.
  function codeNow(secret, then){
    var step = Math.floor(now() / 30000), c = codes[secret];
    if (c && c.step === step) return c.text;
    if (!c || c.want !== step){
      c = codes[secret] = {step: c ? c.step : -1, text: c ? c.text : '', want: step};
      totp(secret, step).then(function(text){
        if (codes[secret] === c){ c.step = step; c.text = text; if (then) then(); }
      }, function(){
        if (codes[secret] === c){ c.step = step; c.text = '------'; if (then) then(); }
      });
    }
    return c.text;
  }
  function codeSec(){ return 30 - Math.floor(now() / 1000) % 30; }

  // ----------------------------------------------------- lines and keys
  // What is on the phone, in the order it is typed. A phone with no Google
  // account has nothing to copy; one with no 2FA key has no code.
  function step(k, label, value, live){
    var code = k === 'code' || k === 'acode', key = live ? 'data-lt-k' : 'data-k';
    var a = {class: 'step', role: 'button', tabindex: '0', 'aria-label': 'Copy the ' + label.toLowerCase()};
    if (live) a['data-lt-copy'] = k; else { a['data-a'] = 'copy'; a['data-w'] = k; }
    var v = {class: 'v' + (code ? ' code tab' : ''), text: value};
    v[key] = k;
    var t = {};
    t[key] = k === 'acode' ? 'atimer' : 'timer';
    return h('div', a, h('span', {class: 'k', text: label}),
      h('span', {class: 'body'}, h('span', v), code ? h('span', {class: 'timer'}, h('i', t)) : null),
      h('span', {class: 'tools'}, h('span', {class: 'cp', 'aria-hidden': 'true', 'data-ic': 'copy'}, icon('copy'))));
  }
  function gmailSteps(d, live){
    if (d.bare) return [h('div', {class: 'step none'}, h('span', {class: 'k', text: 'Google'}),
      h('span', {class: 'body'}, h('span', {class: 'v soft', text: 'No Google account on this phone'})))];
    var out = [step('gmail', 'Gmail', d.gmail || '', live), step('pw', 'Password', d.pw || '', live)];
    if (d.totp) out.push(step('code', 'Code', '', live));
    return out;
  }
  function verdictRow(live){
    return h('div', {class: 'verdict', role: 'group', 'aria-label': 'Close this phone'},
      ...VERDICTS.map(function(v){
        var a = {class: v, type: 'button', 'data-v': v};
        if (live) a['data-lt-v'] = '1'; else a['data-a'] = 'v';
        return h('button', a, h('span', {class: 'c'}, icon(VICON[v]), icon('rim')),
          h('span', {class: 'l', text: WORD[v]}));
      }));
  }
  // Copy, or select the text for Ctrl+C where the clipboard is refused.
  function copyStep(stepEl, text, what, serial, tone){
    function flash(){
      stepEl.classList.add('just'); swapIcon(stepEl, 'ok'); clearTimeout(stepEl._t);
      stepEl._t = setTimeout(function(){ stepEl.classList.remove('just'); swapIcon(stepEl, 'copy'); }, TICK_MS);
    }
    function ok(){ flash(); say(serial + ': ' + COPIED[what] + ' copied', tone); }
    function fall(){
      try {
        var v = stepEl.querySelector('.v'), r = document.createRange();
        r.selectNodeContents(v);
        var sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
      } catch (e) { report(e, 'select'); }
      flash(); say('Selected. Press Ctrl+C to copy.', tone);
    }
    try { navigator.clipboard.writeText(text).then(ok, fall); } catch (e) { fall(); }
  }

  // Two taps close a phone. A double click is one press: the second tap counts
  // only after a moment. True on the confirming tap.
  var armed = null;
  function disarm(){
    if (!armed) return;
    clearTimeout(armed.t); armed.b.classList.remove('armed');
    armed.b.querySelector('.l').textContent = armed.w; armed = null;
  }
  function tap(b){
    if (!armed || armed.b !== b){
      disarm();
      var lw = b.querySelector('.l');
      armed = {b: b, w: lw.textContent, at: Date.now(), t: setTimeout(disarm, 2400)};
      b.classList.add('armed'); lw.textContent = 'Tap again';
      return false;
    }
    if (Date.now() - armed.at < 350) return false;
    disarm();
    return true;
  }

  // The press in flight holds back the pulls, so an older state never lands
  // on top of a newer answer.
  var seq = 0, lastPressApplied = 0, pressing = 0;

  // =================================================== the Station page
  function station(){
    window.name = 'gf-station';
    var S = null, first = true;
    var cards = {}, active = null;
    var tomb = {}, over = {}, pendingV = {}, ipPress = {}, localSeen = {}, everSeen = {};
    var noteSeen = {}, todayIds = null, buildStage = {}, landed = {}, skipLine = {};
    var ghostsB = {}, ghostsL = {};
    var tally = {done: 0, decline: 0, or: 0, auth: 0, failed: 0, all: 0};
    var onProfile = false, editing = null, lastEdit = null, meSig = '', histSig = null;
    var sparks = 0, wantReload = false;
    var bench = $('bench');

    function may(k){ return !!(S && S.may && S.may[k]); }
    function q(c, k){ return c.el.querySelector('[data-k="' + k + '"]'); }
    // While the dialog is up, the page under it cannot be reached - nor
    // an admin's rail beside it (2026-10-02).
    function hold(on){
      [$('bar'), document.querySelector('main'), $('gf-rail')].forEach(function(el){
        if (el){ if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
      });
    }

    // ------------------------------------------------------- applying
    function apply(st, how, note){
      if (!st || typeof st !== 'object' || !st.me) return;
      var isFirst = first;
      first = false;
      if (typeof st.now === 'number') skew = st.now - Date.now();
      var prevToday = todayIds;
      S = st;
      var phones = Array.isArray(st.phones) ? st.phones : [];
      var listed = {};
      phones.forEach(function(p){ listed[p.serial] = p; });
      Object.keys(tomb).forEach(function(s){ if (!listed[s] || tomb[s] <= Date.now()) delete tomb[s]; });
      Object.keys(over).forEach(function(s){
        var o = over[s], p = listed[s];
        if (!p || (p.power && p.power !== 'off') || (o.pressId && p.last && p.last.id >= o.pressId)) delete over[s];
      });
      var today = Array.isArray(st.today) ? st.today : [];
      var newToday = {}, inToday = {};
      todayIds = {};
      today.forEach(function(r){
        todayIds[r.id] = true; inToday[r.serial] = true;
        if (prevToday && !prevToday[r.id]) newToday[r.serial] = r.v;
      });
      // The Results the server counted, less what is still flying to its count.
      var t = st.tally || {};
      tally = {all: 0};
      VERDICTS.forEach(function(v){ tally[v] = num(t[v], 0); tally.all += tally[v]; });
      Object.keys(pendingV).forEach(function(s){
        var v = pendingV[s];
        if (inToday[s] && tally[v] > 0){ tally[v]--; tally.all--; }
      });
      // Phones: new ones arrive, known ones are painted, gone ones leave.
      phones.forEach(function(p, i){
        if (tomb[p.serial]) return;
        var c = cards[p.serial];
        if (c){ if (!c.closing){ c.d = p; paint(c); tickOne(c, codeSec()); } return; }
        addCard(p, i, isFirst, how, note);
      });
      Object.keys(cards).forEach(function(s){
        var c = cards[s];
        if (listed[s] || c.closing) return;
        var v = newToday[s];
        if (v && VICON[v]){ pendingV[s] = v; if (tally[v] > 0){ tally[v]--; tally.all--; } closeCard(c, v, true); }
        else drop(c);
      });
      // Gone from the farm's list: should it come back one day (the line, a
      // build), it arrives with its own words again. A phone given back stays
      // listed until the keeper has put it back, so a card that shows again
      // before that (its tomb ran out) comes back quietly.
      Object.keys(everSeen).forEach(function(s){ if (!listed[s]) delete everSeen[s]; });
      Object.keys(ipPress).forEach(function(s){ ipSettled(s, listed[s]); });
      ghostsApply(st, isFirst);
      (Array.isArray(st.notes) ? st.notes : []).slice().reverse().forEach(function(n){
        if (!n || noteSeen[n.id]) return;
        noteSeen[n.id] = true;
        if (!isFirst) sayLater(n.text, n.tone || n.lane);
      });
      paintMe();
      paintHistory();
      bar();
      if (S.rev && REV && S.rev !== REV){ wantReload = true; reloadIfSettled(); }
    }

    // --------------------------------------------------------- cards
    function stateOf(c){
      var o = over[c.serial], p = o ? o.power : c.d.power;
      return p === 'starting' || p === 'on' || p === 'changing' ? p : 'off';
    }
    function cardEl(c){
      var l = c.lane, s = c.serial;
      var form = h('form', {class: 'boot', method: 'post', action: '/phones/' + s + '/boot', target: 'gf-live-' + s},
        h('input', {type: 'hidden', name: 'csrf', value: csrf()}),
        h('input', {type: 'hidden', name: 'station', value: '1'}),
        h('input', {type: 'hidden', name: 'press', value: ''}),
        h('button', {class: 'open', type: 'submit', 'data-a': 'open'}, icon('power'), h('span', {'data-k': 'open'})));
      return h('section', {class: 'col ' + l, id: 'p-' + s},
        h('div', {class: 'hd'},
          h('span', {class: 'app ' + l, role: 'img', 'aria-label': W(l) + ' phone'}, icon('logo-' + l)),
          h('span', {class: 'meta'}, h('span', {class: 's tab', text: s}), h('span', {class: 'st', 'data-k': 'st'})),
          h('span', {class: 'right'},
            h('span', {class: 'ip', 'data-k': 'ip'}, h('small', {text: 'IP'}), h('span', {'data-k': 'exit'})),
            h('span', {class: 'age'}, icon('clock'), h('span', {class: 'tab', 'data-k': 'age'})))),
        h('div', {class: 'hold'}, h('i', {'data-k': 'hold'})),
        h('div', {class: 'openrow'}, form,
          h('span', {class: 'doors', role: 'group', 'aria-label': 'More for this phone'},
            h('button', {type: 'button', 'data-a': 'ip'}, icon('globe'), h('span', {text: 'Change IP'})),
            h('button', {type: 'button', 'data-a': 'back'}, icon('back'), h('span', {text: 'Give back'})))),
        h('div', {class: 'steps'}),
        verdictRow(false));
    }
    // Whether a sentence names that serial as a word of its own: 5073 is not
    // named by 'Phone 50731 is yours'.
    function names(note, serial){
      if (!serial) return false;
      var t = String(note || ''), at = t.indexOf(serial);
      for (; at >= 0; at = t.indexOf(serial, at + 1)){
        var before = t.charAt(at - 1), after = t.charAt(at + serial.length);
        if (!/[0-9A-Za-z]/.test(before) && !/[0-9A-Za-z]/.test(after)) return true;
      }
      return false;
    }
    // A new card takes the place of the card that was waiting for it when that
    // one leads the waiting cards, and rings once as it lands. It says how it
    // came - built, or from the line - whether a pull or a press's answer
    // brought it, unless that press's own answer already names it.
    function addCard(p, i, isFirst, how, note){
      var c = {serial: String(p.serial), lane: laneOf(p.lane), d: p, el: null};
      var seen = everSeen[c.serial];
      everSeen[c.serial] = true;
      cards[c.serial] = c;
      c.el = cardEl(c);
      var where = null;
      if (p.wish !== null && p.wish !== undefined && ghostsB[p.wish]){
        where = ghostsB[p.wish].el; landed[p.wish] = true; delete ghostsB[p.wish];
      } else if (p.arrived === 'line' && ghostsL[c.lane]){
        where = ghostsL[c.lane].el; skipLine[c.lane] = ghostsL[c.lane].id; delete ghostsL[c.lane];
      }
      if (isFirst) c.el.style.animationDelay = (i * 70) + 'ms';
      else c.el.classList.add('arrive');
      if (where && where.parentNode === bench && where === bench.querySelector('.ghost')) bench.replaceChild(c.el, where);
      else {
        if (where && where.parentNode) where.parentNode.removeChild(where);
        bench.insertBefore(c.el, bench.querySelector('.ghost'));
      }
      paint(c); tickOne(c, codeSec());
      if (isFirst) return;
      setActive(c); scrollTo(c.el);
      if (seen || (how === 'press' && names(note, c.serial))) return;
      var WL = c.lane === 'other' ? '' : W(c.lane) + ' ';
      if (p.arrived === 'build'){
        if (p.called_off) sayLater('Your ' + WL + 'phone ' + c.serial + ' was already signed in, so it was kept. Press Boot to switch it on.', c.lane);
        else sayLater('Your ' + WL + 'phone ' + c.serial + ' is built. Press Boot to switch it on.', c.lane);
      } else if (p.arrived === 'line'){
        sayLater('Your ' + W(c.lane) + ' phone is here: ' + c.serial + '. Press Boot to switch it on.', c.lane);
      }
    }
    var ST = {off: 'Ready', starting: 'Booting', on: 'On', changing: 'Changing IP'};
    var OPEN = {off: 'Boot', starting: 'Booting…', on: 'Boot', changing: 'Changing IP…'};
    function paint(c){
      var el = c.el, d = c.d, st = stateOf(c);
      if (!el) return;
      el.dataset.state = st;
      el.dataset.power = st === 'on' ? 'on' : (st === 'starting' || st === 'changing') ? 'busy' : 'ready';
      q(c, 'st').textContent = ST[st];
      q(c, 'open').textContent = OPEN[st];
      el.querySelector('.open').disabled = st === 'starting' || st === 'changing';
      el.querySelector('[data-a="ip"]').disabled = (st !== 'on' && st !== 'off') || !may('ip');
      el.querySelector('[data-a="back"]').disabled = !may('take');
      el.querySelectorAll('.verdict button').forEach(function(b){ b.disabled = !may('take'); });
      var ex = d.exit || '—';
      if (c.exit !== undefined && c.exit !== ex){
        var ip = q(c, 'ip'); ip.classList.remove('new'); void ip.offsetWidth; ip.classList.add('new');
      }
      c.exit = ex; q(c, 'exit').textContent = ex;
      var sig = [d.bare ? 1 : 0, d.gmail || '', d.pw || '', d.totp ? 1 : 0].join('\u0001');
      if (c.sig !== sig){
        c.sig = sig;
        fill(el.querySelector('.steps'), ...gmailSteps(d, false));
      }
    }
    // Each phone's clock: the hour it is left alone, and its code.
    function tickOne(c, sec){
      if (!c.el || c.closing || !S) return;
      var d = c.d, t = now(), hm = num(S.hold_minutes, 0);
      var held = Math.floor((t - num(d.taken_at, t)) / MIN);
      var idle = d.watching || d.idle_since === null || d.idle_since === undefined
        ? 0 : Math.max(0, Math.floor((t - d.idle_since) / MIN));
      var bar0 = q(c, 'hold'), late = false, age = held < 1 ? 'just now' : held + ' min';
      bar0.parentNode.hidden = hm <= 0;
      if (hm > 0){
        var left = hm - idle;
        bar0.style.width = Math.min(100, idle / hm * 100) + '%';
        late = left <= num(S.late_minutes, 15);
        if (late) age = 'back in ' + Math.max(0, left) + ' min';
      }
      c.el.classList.toggle('late', late);
      q(c, 'age').textContent = age;
      var ce = q(c, 'code');
      if (!ce || !d.totp) return;
      var text = codeNow(d.totp, function(){ if (cards[c.serial] === c) tickOne(c, codeSec()); });
      if (text) setCode(ce, text);
      c.el.querySelector('.step[data-w="code"]').classList.toggle('late', sec <= 5);
      q(c, 'timer').style.transform = 'scaleX(' + (sec / 30) + ')';
    }
    function setActive(c){
      if (active && active.el) active.el.classList.remove('active');
      active = c;
      if (c && c.el) c.el.classList.add('active');
    }
    function leave(el){
      el.style.width = el.offsetWidth + 'px'; el.classList.add('out');
      setTimeout(function(){
        el.style.width = '0px'; el.style.paddingLeft = '0px'; el.style.paddingRight = '0px';
        el.style.borderWidth = '0px'; el.style.marginRight = '-20px';
      }, reduced ? 0 : 220);
      setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); bar(); }, reduced ? 0 : 560);
    }
    // Where focus goes when the thing it was on is gone: the next phone's Boot,
    // else a way to get one.
    function focusNext(except){
      var nx = null;
      bench.querySelectorAll('.col:not(.ghost)').forEach(function(el){
        if (nx || el === except.el) return;
        var c = cards[(el.id || '').slice(2)];
        if (c && c.el === el && !c.closing) nx = c;
      });
      focusQuiet(nx ? nx.el.querySelector('.open') : document.querySelector('.takes button:not([disabled])'));
    }
    function drop(c){
      var el = c.el;
      if (!el) return;
      var had = el.contains(document.activeElement);
      clearTimeout(c.closeT);
      if (cards[c.serial] === c) delete cards[c.serial];
      if (active === c) active = null;
      c.el = null;
      leave(el); bar();
      if (had) focusNext({el: el});
    }
    // A verdict stamps the card with its colour and word, then the card folds away.
    function closeCard(c, v, spark){
      var el = c.el;
      if (!el) return;
      c.closing = true; el.classList.add('closing');
      if (active === c) setActive(null);
      c.stamp = h('div', {class: 'stamp ' + v}, h('span', {class: 'c'}, icon(VICON[v])), h('b', {text: WORD[v]}));
      el.appendChild(c.stamp);
      if (spark){
        var s = c.serial, key = el.querySelector('.verdict [data-v="' + v + '"] .c');
        if (!fly(key, $('t-' + v), v, function(){ counted(s, v); })) counted(s, v);
      }
      c.closeT = setTimeout(function(){ drop(c); }, reduced ? 0 : 700);
    }
    // The count goes up as the spark from the key reaches it.
    function counted(s, v){
      if (pendingV[s] !== v) return;
      delete pendingV[s];
      tally[v]++; tally.all++;
      bar();
    }
    // A small spark from the key that was pressed to where it counts, along a
    // curve that rises first; done() runs as it arrives. False when it cannot fly.
    function fly(from, to, v, done){
      if (reduced || !from || !to || !from.animate) return false;
      var a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
      if (!a.width || !b.width) return false;
      var x0 = a.left + a.width / 2, y0 = a.top + a.height / 2, x1 = b.left + b.width / 2, y1 = b.top + b.height / 2;
      var cx = x0 + (x1 - x0) * .12, cy = y1 + (y0 - y1) * .18, frames = [];
      for (var i = 0; i <= 10; i++){
        var t = i / 10, u = 1 - t;
        frames.push({opacity: i ? 1 : 0,
          transform: 'translate(' + (u * u * x0 + 2 * u * t * cx + t * t * x1) + 'px,' +
            (u * u * y0 + 2 * u * t * cy + t * t * y1) + 'px) scale(' + (i ? i === 10 ? .55 : 1 : .4) + ')'});
      }
      var sp = h('i', {class: 'spark ' + v});
      document.body.appendChild(sp);
      sparks++;
      sp.animate(frames, {duration: 720, easing: 'cubic-bezier(.45,.05,.3,1)'}).onfinish = function(){
        sparks--; if (sp.parentNode) sp.parentNode.removeChild(sp); if (done) done();
      };
      return true;
    }
    function cardOf(el){
      var col = el.closest && el.closest('.col:not(.ghost)');
      if (!col) return null;
      var c = cards[(col.id || '').slice(2)];
      return c && c.el === col ? c : null;
    }

    // --------------------------------------------------------- ghosts
    function orb(l){
      return h('div', {class: 'g-orb'}, h('span', {class: 'g-ring'}), h('span', {class: 'app ' + l}, icon('logo-' + l)));
    }
    function placeGhost(g){
      var line = !!g.getAttribute('data-line'), id = +g.getAttribute('data-wish') || 0, before = null;
      bench.querySelectorAll('.ghost').forEach(function(x){
        if (before || x === g) return;
        var xw = +x.getAttribute('data-wish') || 0;
        if (line ? xw > 0 : xw > id) before = x;
      });
      bench.insertBefore(g, before);
    }
    function lineMinutes(l, n){
      var s = (S.shelves || {})[l] || {}, late = num(s.late, 0), t = now();
      if (n <= late) return 1;
      var etas = (Array.isArray(s.etas) ? s.etas : []).filter(function(e){ return e > t; });
      var i = n - late - 1;
      if (i < etas.length) return Math.max(1, Math.ceil((etas[i] - t) / 60000));
      return num(s.typical_min, 6) * n;
    }
    function buildMinutes(b){
      if (b.late) return 1;
      if (b.stage === 'building' && b.eta_at) return Math.max(1, Math.ceil((b.eta_at - now()) / 60000));
      return num(b.typical_min, 6);
    }
    function paintLine(g, l, line){
      var n = num(line.position, 1);
      g._line = {l: l, n: n};
      if (g._sig === n) return;
      g._sig = n;
      fill(g, orb(l),
        h('div', {class: 'g-t'}, h('b', {text: n === 1 ? 'You are first in line' : 'You are number ' + n + ' in line'}),
          h('small', {text: 'The next ' + W(l) + ' phone is yours. It lands here the moment it is ready.'})),
        h('div', {class: 'g-eta'}, h('b', {class: 'tab', 'data-eta': ''}), h('small', {text: 'min left'})),
        h('div', {class: 'g-bar'}, h('i')),
        h('button', {class: 'g-off', type: 'button', 'data-a': 'unqueue', 'data-l': l, text: 'Leave the line'}));
    }
    function paintBuild(g, b){
      var l = laneOf(b.lane), chips = Array.isArray(b.chips) ? b.chips : [];
      g._b = b;
      var sig = [b.stage, b.called_off ? 1 : 0, b.reason || '', b.bare ? 1 : 0, chips.join('\u0001')].join('|');
      if (g._sig === sig) return;
      g._sig = sig;
      var WL = l === 'other' ? '' : W(l) + ' ', failed = b.stage === 'failed';
      g.classList.toggle('failed', failed);
      var chipEl = chips.length ? h('div', {class: 'g-chips'}, ...chips.map(function(x){ return h('span', {text: String(x)}); })) : null;
      var sub = (b.bare ? 'With no Google account. ' : '') + 'It lands here the moment it is ready.';
      if (failed) fill(g, orb(l),
        h('div', {class: 'g-t'}, h('b', {text: 'Your ' + WL + 'phone was not built'}), h('small', {text: b.reason || ''})),
        chipEl, h('button', {class: 'g-off', type: 'button', 'data-a': 'dismiss', 'data-id': b.id, text: 'Dismiss'}));
      else fill(g, orb(l),
        h('div', {class: 'g-t'}, h('b', {text: b.called_off ? 'Calling the build off' : 'Building your ' + WL + 'phone'}), h('small', {text: sub})),
        h('div', {class: 'g-eta', hidden: !!b.called_off}, h('b', {class: 'tab', 'data-eta': ''}), h('small', {text: 'min left'})),
        h('div', {class: 'g-bar'}, h('i')), chipEl,
        b.called_off ? null : h('button', {class: 'g-off', type: 'button', 'data-a': 'unbuild', 'data-id': b.id, text: 'Call the build off'}));
    }
    // The minutes left on each card of a phone on its way, never below one.
    function ghostClock(){
      bench.querySelectorAll('.ghost').forEach(function(g){
        var e = g.querySelector('[data-eta]');
        if (!e) return;
        var m = g._line ? lineMinutes(g._line.l, g._line.n) : g._b ? buildMinutes(g._b) : null;
        if (m !== null) e.textContent = String(Math.max(1, m));
      });
    }
    function ghostsApply(st, isFirst){
      var sh = st.shelves || {};
      ['gpt', 'spotify'].forEach(function(l){
        var line = sh[l] && sh[l].line, have = ghostsL[l];
        if (line && line.id !== skipLine[l]){
          if (!have){
            var g = h('section', {class: 'col ghost ' + l, id: 'l-' + l, 'data-line': l});
            paintLine(g, l, line);
            ghostsL[l] = {el: g, id: line.id};
            placeGhost(g);
            if (!isFirst) scrollTo(g);
          } else { have.id = line.id; paintLine(have.el, l, line); }
        } else if (have){ delete ghostsL[l]; leave(have.el); }
        if (!line) delete skipLine[l];
      });
      var want = {};
      (Array.isArray(st.builds) ? st.builds : []).forEach(function(b){
        if (landed[b.id]) return;
        want[b.id] = true;
        var was = buildStage[b.id], have = ghostsB[b.id];
        buildStage[b.id] = b.stage;
        if (!have){
          var g = h('section', {class: 'col ghost ' + laneOf(b.lane), id: 'w-' + b.id, 'data-wish': b.id});
          paintBuild(g, b);
          ghostsB[b.id] = {el: g};
          placeGhost(g);
          if (!isFirst) scrollTo(g);
        } else paintBuild(have.el, b);
        if (!isFirst && b.stage === 'failed' && was && was !== 'failed'){
          var l = laneOf(b.lane);
          sayLater('Your ' + (l === 'other' ? '' : W(l) + ' ') + 'phone was not built.', true);
        }
      });
      Object.keys(ghostsB).forEach(function(id){
        if (want[id]) return;
        var g = ghostsB[id];
        delete ghostsB[id];
        leave(g.el);
      });
      ghostClock();
    }

    // ------------------------------------------------------------- bar
    function etaMin(s){
      if (num(s.late, 0) > 0) return 1;
      if (s.eta_at) return Math.max(1, Math.ceil((s.eta_at - now()) / 60000));
      return num(s.typical_min, 6);
    }
    function bar(){
      if (!S) return;
      var sh = S.shelves || {};
      ['gpt', 'spotify'].forEach(function(l){
        var s = sh[l] || {}, b = $('take-' + l), w = b.querySelector('.w'), sub = b.querySelector('.sub');
        var c = b.querySelector('.c'), was = c.textContent;
        var ready = num(s.ready, 0), building = num(s.building, 0), kind, word = '', text = '', aria, dis = false;
        if (s.line){
          var n = num(s.line.position, 1);
          kind = 'inline'; word = 'In line'; text = n === 1 ? 'you are next' : 'number ' + n + ' in line'; dis = true;
          aria = 'In line for the next ' + W(l) + ' phone';
        } else if (ready > 0){
          kind = 'ready'; word = 'Take ' + W(l);
          text = building ? '+' + building + ' in ' + etaMin(s) + ' min' : (l === 'gpt' ? 'ChatGPT · Claude' : '');
          aria = 'Take a ' + W(l) + ' phone, ' + ready + ' ready';
        } else if (building > 0){
          kind = 'soon'; word = 'Wait for ' + W(l); text = 'next one in ' + etaMin(s) + ' min';
          aria = 'Wait in line for a ' + W(l) + ' phone';
        } else {
          kind = 'none'; word = 'No ' + W(l) + ' phone'; text = 'shelf is empty'; dis = true;
          aria = 'No ' + W(l) + ' phone on the shelf';
        }
        b.classList.toggle('soon', kind === 'soon'); b.classList.toggle('inline', kind === 'inline');
        b.disabled = dis || !may('take');
        c.textContent = String(ready);
        if (b._sig !== kind + word){
          b._sig = kind + word;
          if (kind === 'ready') fill(w, h('span', {class: 'tk', text: 'Take '}), W(l));
          else w.textContent = word;
        }
        if (sub.textContent !== text) sub.textContent = text;
        b.setAttribute('aria-label', aria);
        if (kind === 'ready' && was !== String(ready)) bump(c);
      });
      [['t-done', 'done'], ['t-decline', 'decline'], ['t-or', 'or'], ['t-auth', 'auth'], ['t-failed', 'failed']].forEach(function(x){
        var el = $(x[0]);
        if (el.textContent !== String(tally[x[1]])){ el.textContent = String(tally[x[1]]); bump(el); }
      });
      // The ring: each verdict's share of the day, clockwise from the top,
      // with a hair of space between the arcs.
      var kinds = [['seg-d', 'done'], ['seg-x', 'decline'], ['seg-o', 'or'], ['seg-a', 'auth'], ['seg-f', 'failed']], off = 0;
      var all = kinds.reduce(function(n, x){ return n + tally[x[1]]; }, 0);
      var shown = kinds.filter(function(x){ return tally[x[1]] > 0; }).length, gap = shown > 1 ? 3 : 0;
      kinds.forEach(function(x){
        var c = $(x[0]), pct = all ? tally[x[1]] / all * 100 : 0, len = Math.max(0.01, pct - gap);
        c.style.opacity = pct > 0 ? '1' : '0';
        c.setAttribute('stroke-dasharray', len + ' ' + (100 - len));
        c.setAttribute('stroke-dashoffset', String(-(off + gap / 2)));
        off += pct;
      });
      var tot = $('t-all');
      if (tot.textContent !== String(all)){ tot.textContent = String(all); bump(tot); }
      $('build').hidden = !may('build');
      $('empty').hidden = onProfile || !!bench.querySelector('.col');
    }

    // ------------------------------------------------------ the presses
    function send(path, fields, pre){
      pressing++;
      var turn = ++seq;
      return api(path, fields).then(function(a){
        pressing--;
        // A pull sent before this press never lands on top of its answer.
        lastPressApplied = Math.max(lastPressApplied, turn);
        try { if (pre) pre(a); } catch (e) { report(e, 'press'); }
        if (a && a.state){
          try { apply(a.state, 'press', a.note); } catch (e) { report(e, 'apply'); }
        } else pullWanted = true;
        if (!pressing && pullWanted) schedulePull();
        return a;
      });
    }
    function take(l){
      send('/station/take', {lane: l}).then(function(a){
        if (a.said === 'took' || a.said === 'in-line' || a.said === 'twice') say(a.note, l);
        else say(a.note || BROKE, true);
      });
    }
    function bootSubmit(e, form){
      var c = cardOf(form);
      disarm();
      if (!c || c.closing){ e.preventDefault(); return; }
      setActive(c);
      var st = stateOf(c), s = c.serial;
      if (st === 'on'){ e.preventDefault(); openTab(s); return; }
      if (st !== 'off'){ e.preventDefault(); return; }
      form.querySelector('[name="press"]').value = press();
      form.querySelector('[name="csrf"]').value = csrf();
      var again = !!(c.d.tab_seen || localSeen[s]);
      localSeen[s] = true;
      over[s] = {power: 'starting', until: Date.now() + 15000};
      paint(c);
      say(again ? 'Booting ' + s + ' again. It stays yours.' : 'Booting ' + s + '. Its screen opens in its own tab.', c.lane);
    }
    // An on phone's tab, opened or brought forward without starting it again.
    function openTab(s){
      var w = window.open('', 'gf-live-' + s);
      var path = '';
      try { path = w ? w.location.pathname : ''; } catch (e) { path = ''; }
      // about:blank reads as 'blank', and a tab gone to another site as ''.
      if (w && path !== '/station/phones/' + s) w.location.href = '/station/phones/' + s;
      if (w) w.focus();
    }
    function changeIp(c){
      var then = stateOf(c), s = c.serial;
      if (then !== 'on' && then !== 'off') return;
      over[s] = {power: 'changing', until: Date.now() + 180000};
      ipPress[s] = {then: then, pressId: 0};
      paint(c);
      say(then === 'off' ? 'Changing the IP of ' + s + '. It stays ready to boot.'
        : 'Changing the IP of ' + s + '. Its screen comes back by itself.', c.lane);
      send('/phones/' + s + '/proxy', {station: 1, keep_power: 1, was: then === 'on' ? 'on' : 'off'}, function(a){
        if (a.ok && a.req){ if (ipPress[s]) ipPress[s].pressId = a.req; if (over[s]) over[s].pressId = a.req; }
        else if (!a.ok){ delete over[s]; delete ipPress[s]; }
      }).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); if (cards[s]) paint(cards[s]); }
      });
    }
    // Change IP pressed here: said once, when the phone's last press is it,
    // in the card's own sentence (the Live tab has its longer ones).
    function ipSettled(s, p){
      var ip = ipPress[s];
      if (!p){ delete ipPress[s]; return; }
      if (!ip.pressId || !p.last || p.last.id < ip.pressId) return;
      delete ipPress[s];
      var last = p.last;
      if (last.verb && last.verb !== 'change_proxy') return;
      if (!last.ok) say(last.note || 'The IP of ' + s + ' did not change.', true);
      else say(s + ' moved from ' + last.was + ' to ' + last.now + '.', laneOf(p.lane));
    }
    function giveBack(c){
      var s = c.serial, l = c.lane;
      tomb[s] = Date.now() + 60000;
      drop(c);
      say('Phone ' + s + ' is back on ' + home(l) + '.', l);
      send('/station/phones/' + s + '/back', {where: 'station'}, function(a){
        if (!a.ok) delete tomb[s];
      }).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); if (!a.state) schedulePull(); }
      });
    }
    function verdict(c, b){
      if (!c.el || c.closing) return;
      if (!tap(b)) return;
      var v = b.getAttribute('data-v'), s = c.serial;
      pendingV[s] = v;
      tomb[s] = Date.now() + 60000;
      say('Phone ' + s + ' closed as ' + WORD[v] + '. It is deleted in a moment.', v);
      closeCard(c, v, true);
      send('/phones/' + s + '/state', {state: v, sure: 1, where: 'station'}, function(a){
        if (a.ok) return;
        delete tomb[s];
        if (pendingV[s] === v) delete pendingV[s];
        else if (!a.state){ tally[v] = Math.max(0, tally[v] - 1); tally.all = Math.max(0, tally.all - 1); }
        if (cards[s] === c && c.el){
          clearTimeout(c.closeT); c.closing = false; c.el.classList.remove('closing');
          if (c.stamp && c.stamp.parentNode) c.stamp.parentNode.removeChild(c.stamp);
          c.stamp = null;
        }
      }).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); bar(); if (!a.state) schedulePull(); }
      });
    }
    function act(c, a, b){
      if (a !== 'v') disarm();
      if (a === 'v') verdict(c, b);
      else if (a === 'copy') copyCard(c, b);
      else if (a === 'ip'){ if (!b.disabled) changeIp(c); }
      else if (a === 'back'){ if (!b.disabled) giveBack(c); }
    }
    function copyCard(c, st){
      var k = st.getAttribute('data-w'), d = c.d;
      var text = k === 'gmail' ? d.gmail : k === 'pw' ? d.pw : (q(c, 'code').textContent || '').replace(' ', '');
      copyStep(st, text || '', k, c.serial, c.lane);
    }
    function leaveLine(l){
      send('/station/line/leave', {lane: l}).then(function(a){
        if (a.ok || a.said === 'no') say(a.note, l); else say(a.note || BROKE, true);
      });
    }
    function callOff(g, id){
      send('/station/builds/' + id + '/off', {}).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); return; }
        var b = g._b;
        if (b && g.parentNode) paintBuild(g, Object.assign({}, b, {called_off: true}));
        say('The build was called off.', b ? laneOf(b.lane) : 'other');
      });
    }
    function dismiss(g, id){
      send('/wishes/' + id + '/dismiss', {}).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); return; }
        if (ghostsB[id] && ghostsB[id].el === g){ delete ghostsB[id]; landed[id] = true; }
        if (g.parentNode && !g.classList.contains('out')) leave(g);
      });
    }

    // ---------------------------------------------------- hearing the farm
    var es = null, lastRev, pullAt = 0, pullT = null, pulling = false, pullWanted = false, etag = '', outStrikes = 0;
    function schedulePull(){
      pullWanted = true;
      if (pullT || pulling || pressing) return;
      pullT = setTimeout(function(){ pullT = null; pull(); }, Math.max(0, pullAt + 2000 - Date.now()));
    }
    function pull(){
      if (pulling || pressing) return;
      pullWanted = false; pulling = true; pullAt = Date.now();
      var turn = ++seq, hd = {};
      if (etag) hd['If-None-Match'] = etag;
      api('/station/state', null, {method: 'GET', raw: true, headers: hd}).then(function(r){
        if (!r) return null;
        // A 200 or a 304 is a pull still signed in.
        if (r.status === 200 || r.status === 304) outStrikes = 0;
        if (r.status === 304) return null;
        // One signed-out pull may be a blip: asked once more before the page
        // follows it to the sign-in.
        if (r.status === 401 && ++outStrikes < 2){ pullWanted = true; return null; }
        return readJSON(r).then(function(st){
          if (r.status !== 200 || !st || !st.me) return null;
          var tag = r.headers && r.headers.get ? r.headers.get('ETag') : '';
          if (tag) etag = tag;
          return st;
        });
      }).then(function(st){
        pulling = false;
        if (st){
          if (pressing || turn < lastPressApplied) pullWanted = true;
          else apply(st, 'pull');
        }
        if (pullWanted) schedulePull();
      }, function(err){ pulling = false; report(err, 'pull'); });
    }
    function listen(){
      if (typeof EventSource === 'undefined') return;
      try { es = new EventSource('/live'); } catch (e) { es = null; return; }
      es.onmessage = function(e){
        var n = parseInt(e.data, 10);
        if (isNaN(n)) return;
        // The first message is the revision at connect time, later than the
        // page's own state: one pull covers what changed in between.
        if (lastRev === undefined){ lastRev = n; schedulePull(); return; }
        if (n !== lastRev){ lastRev = n; schedulePull(); }
      };
    }
    function settled(){
      var a = document.activeElement;
      return $('scrim').hidden && !armed && !editing && !pressing && !sparks &&
        !document.querySelector('.step.just') &&
        !(a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT'));
    }
    function reloadIfSettled(){ if (wantReload && settled()){ wantReload = false; location.reload(); } }
    var secs = 0;
    function tick1s(){
      secs++;
      var sec = codeSec();
      Object.keys(cards).forEach(function(s){ tickOne(cards[s], sec); });
      Object.keys(over).forEach(function(s){
        if (over[s].until > Date.now()) return;
        delete over[s];
        if (cards[s]) paint(cards[s]);
        schedulePull();
      });
      ghostClock();
      bar();
      if (secs % 10 === 0 && (!es || es.readyState === 2)) schedulePull();
      if (secs % 30 === 0) schedulePull();
      reloadIfSettled();
    }

    // ------------------------------------------------------- building
    // Each setting is one small choice: what it means is said under it, and
    // Manual opens a field in its place, with the format to type.
    var mode = {gmail: 'auto', acct: 'none', ip: 'auto'}, buildLane = 'gpt', building = false, downOnScrim = false;
    // Each opening of the dialog is its own: a late answer to a Build pressed
    // in an earlier one never closes or marks this one.
    var dlgGen = 0;
    var FORM = {gmail: [/^[^\s:@]+@[^\s:@]+\.[^\s:@]+:[^\s:]+(:.+)?$/, 'Type it as email:password:2FA key.'],
      acct: [/^[^\s:@]+@[^\s:@]+\.[^\s:@]+(:\S+)?$/, 'Type it as email:password, or the email alone.'],
      ip: [/^[A-Za-z0-9.-]+:\d{2,5}(:[^\s:]+:[^\s:]+)?$/, 'Type it as host:port:user:password.']};
    function bf(){ return (S && S.build_form) || {}; }
    function freeIps(l){ var f = bf().free_ips; return f && typeof f[l] === 'number' ? f[l] : null; }
    function meaning(k){
      var m = mode[k], l = buildLane, WL = l === 'other' ? '' : W(l) + ' ';
      if (k === 'gmail'){
        var n = bf().gmails_left;
        if (m === 'auto' && n === 0) return [true, 'No free Gmail in the pool – type one under Manual, or pick None.'];
        if (m === 'auto') return typeof n === 'number' ? [false, 'Next free Gmail from the pool · ', h('b', {text: String(n)}), ' left'] : [false, 'Next free Gmail from the pool'];
        return m === 'none' ? [false, 'A bare phone – nothing signed into Google'] : [false];
      }
      if (k === 'acct') return m === 'none' ? [false, 'No app account – sign in by hand later'] : [false];
      if (m === 'auto' && freeIps(l) === 0) return [true, 'No free ' + WL + 'IP – type one under Manual.'];
      return m === 'auto' ? [false, 'The least-used free ' + WL + 'IP'] : [false, 'For this phone only – it never joins the pool'];
    }
    function setting(k){ return document.querySelector('.dlg [data-row="' + k + '"]'); }
    function tell(k, parts){
      var sy = setting(k).querySelector('.say');
      fill(sy, ...parts.slice(1));
      sy.classList.toggle('bad', !!parts[0]);
      sy.style.animation = 'none'; void sy.offsetWidth; sy.style.animation = '';
    }
    function setMode(k, m, focus){
      mode[k] = m;
      var r = setting(k);
      r.querySelectorAll('[data-m]').forEach(function(b, i){
        var on = b.getAttribute('data-m') === m;
        b.setAttribute('aria-checked', String(on));
        if (on) r.querySelector('.seg2').style.setProperty('--i', String(i));
      });
      var f = r.querySelector('.field'), inp = f.querySelector('.typed');
      f.hidden = m !== 'manual'; inp.classList.remove('bad'); tell(k, meaning(k));
      if (m === 'manual' && focus) setTimeout(function(){ inp.focus(); }, 20);
    }
    // A tick in the field once what is typed reads right; a refusal says the format.
    function check(k){
      var inp = $('f-' + k);
      setting(k).querySelector('.field').classList.toggle('good', FORM[k][0].test(inp.value.trim()));
      if (inp.classList.contains('bad')){ inp.classList.remove('bad'); tell(k, meaning(k)); }
    }
    function refuse(k, why){
      var r = setting(k);
      if (!r){ fill(document.querySelector('[data-dlg="said"]'), why); return false; }
      tell(k, [true, why]);
      if (mode[k] === 'manual'){ var inp = $('f-' + k); inp.classList.add('bad'); inp.focus(); }
      else { var on = r.querySelector('[data-m][aria-checked="true"]'); if (on) on.focus(); }
      return false;
    }
    function typicalMin(l){ var t = bf().typical_min; return t && typeof t[l] === 'number' ? t[l] : 6; }
    function pickLane(l){
      buildLane = l; $('dlg').dataset.lane = l;
      document.querySelectorAll('.dlg [data-pick]').forEach(function(b){ b.setAttribute('aria-checked', String(b.getAttribute('data-pick') === l)); });
      document.querySelector('[data-dlg="eta"]').textContent = 'about ' + typicalMin(l) + ' min';
      if (mode.ip === 'auto') tell('ip', meaning('ip'));
    }
    function openBuild(){
      showProfile(false); disarm();
      dlgGen++;
      document.querySelector('.dlg .go').disabled = building;
      document.querySelectorAll('.dlg [data-logo]').forEach(function(t){ if (!t.firstChild) t.appendChild(icon('logo-' + t.getAttribute('data-logo'))); });
      document.querySelectorAll('.dlg .typed').forEach(function(i){ i.value = ''; i.parentNode.classList.remove('good'); });
      fill(document.querySelector('[data-dlg="said"]'));
      setMode('gmail', 'auto'); setMode('acct', 'none'); setMode('ip', 'auto');
      pickLane(buildLane);
      document.querySelector('[data-dlg="stopped"]').hidden = !bf().stopped;
      $('scrim').hidden = false; hold(true);
      setTimeout(function(){ focusQuiet(document.querySelector('.dlg .kinds [aria-checked="true"]')); }, 30);
    }
    function closeBuild(){ $('scrim').hidden = true; hold(false); focusQuiet($('build')); reloadIfSettled(); }
    function startBuild(){
      if (building) return;
      var l = buildLane, typed = {}, keys = ['gmail', 'acct', 'ip'];
      fill(document.querySelector('[data-dlg="said"]'));
      for (var i = 0; i < keys.length; i++){
        var k = keys[i];
        typed[k] = $('f-' + k).value.trim();
        if (mode[k] === 'manual' && !FORM[k][0].test(typed[k])) return refuse(k, FORM[k][1]);
      }
      if (l === 'gpt' && mode.acct === 'manual' && mode.gmail === 'none')
        return refuse('acct', 'A GPT account needs a Gmail on the phone – set Gmail to Auto or Manual.');
      if (l === 'spotify' && mode.acct === 'manual' && mode.gmail !== 'none')
        return refuse('acct', 'A Spotify account goes on a phone with no Gmail – set Gmail to None.');
      if (l === 'spotify' && mode.acct === 'manual' && typed.acct.indexOf(':') < 0)
        return refuse('acct', 'A Spotify account needs its password: email:password.');
      if (mode.gmail === 'auto' && bf().gmails_left === 0)
        return refuse('gmail', 'No free Gmail in the pool – type one under Manual, or pick None.');
      if (mode.ip === 'auto' && freeIps(l) === 0)
        return refuse('ip', l === 'other' ? 'No free IP – type one under Manual.' : 'No free ' + W(l) + ' IP – type one under Manual.');
      building = true;
      var go = document.querySelector('.dlg .go'), gen = dlgGen;
      go.disabled = true;
      send('/station/build', {kind: l,
        gmail_mode: mode.gmail, gmail_line: mode.gmail === 'manual' ? typed.gmail : '',
        acct_mode: mode.acct, acct_line: mode.acct === 'manual' ? typed.acct : '',
        ip_mode: mode.ip, ip_line: mode.ip === 'manual' ? typed.ip : ''}).then(function(a){
        building = false; go.disabled = false;
        var open = !$('scrim').hidden && gen === dlgGen;
        if (a.ok){
          if (open) closeBuild();
          var m = typicalMin(l);
          say((l === 'other' ? 'Building a phone for you' : 'Building a ' + W(l) + ' phone for you') +
            ' – about ' + m + (m === 1 ? ' minute.' : ' minutes.'), l);
          return;
        }
        // Closed while the answer was out: the refusal is said on the page.
        if (!open){ say(a.note || BROKE, true); return; }
        if (a.field && setting(a.field)) refuse(a.field, a.note || BROKE);
        else fill(document.querySelector('[data-dlg="said"]'), a.note || BROKE);
      });
    }

    // -------------------------------------------------- your own page
    function me(){ return (S && S.me) || {}; }
    var HELLO = {morning: 'Good morning', afternoon: 'Good afternoon', evening: 'Good evening', night: 'Hello'};
    function showProfile(on){
      if (on === onProfile) return;
      onProfile = on; editing = null; disarm();
      $('profile').hidden = !on; bench.hidden = on; document.body.classList.toggle('on-profile', on);
      if (on){
        paintMe(); renderAccount(true); paintHistory(true);
        window.scrollTo(0, 0);
        setTimeout(function(){ focusQuiet(document.querySelector('.p-back')); }, 30);
      }
      bar();
    }
    function paintMe(){
      var m = me(), name = m.name || m.user || '';
      function each(k, text){ document.querySelectorAll('[data-me="' + k + '"]').forEach(function(el){ el.textContent = text; }); }
      each('hello', (HELLO[m.daypart] || 'Hello') + ', ' + name);
      each('name', name);
      each('initial', m.initial || name.charAt(0).toUpperCase());
      each('handle', '@' + (m.user || ''));
      each('since', m.since || '');
      var sig = [name, m.user, m.pw].join('\u0001');
      if (sig !== meSig){ meSig = sig; if (!editing) renderAccount(true); }
    }
    function pline(k, label, value, soft){
      if (editing === k) return h('div', {class: 'pl editing'}, h('span', {class: 'k', text: label}), editForm(k));
      return h('div', {class: 'pl'}, h('span', {class: 'k', text: label}),
        h('span', {class: 'v' + (soft ? ' soft' : ''), text: value}),
        h('button', {type: 'button', class: 'edit', 'data-edit': k, text: 'Change'}));
    }
    function editForm(k){
      var m = me();
      var acts = h('span', {class: 'acts'}, h('button', {type: 'button', class: 'nope', 'data-edit-cancel': '1', text: 'Cancel'}),
        h('button', {type: 'submit', class: 'save', text: 'Save'}));
      if (k === 'name') return h('form', {class: 'pform', 'data-form': 'name', novalidate: true},
        h('input', {name: 'a', value: m.name || '', maxlength: '24', autocomplete: 'off', 'aria-label': 'Your name'}),
        h('span', {class: 'hint', text: 'What the station and the console call you.'}), acts);
      if (k === 'user') return h('form', {class: 'pform', 'data-form': 'user', novalidate: true},
        h('input', {name: 'a', value: m.user || '', maxlength: '20', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Your username'}),
        h('span', {class: 'hint', text: 'You sign in with it. Small letters, digits, dots and underscores.'}), acts);
      return h('form', {class: 'pform', 'data-form': 'pw', novalidate: true},
        h('input', {name: 'a', type: 'password', placeholder: 'Current password', autocomplete: 'current-password', 'aria-label': 'Current password'}),
        h('input', {name: 'b', type: 'password', placeholder: 'New password', autocomplete: 'new-password', 'aria-label': 'New password'}),
        h('input', {name: 'c', type: 'password', placeholder: 'New password again', autocomplete: 'new-password', 'aria-label': 'New password again'}),
        h('span', {class: 'hint', text: 'At least 8 characters. Your other browsers are signed out when it changes.'}), acts);
    }
    function renderAccount(quiet){
      var m = me();
      fill($('acct'), pline('name', 'Name', m.name || ''), pline('user', 'Username', m.user || ''),
        pline('pw', 'Password', m.pw || '', true));
      if (quiet) return;
      var f = $('acct').querySelector('.pform input');
      if (f) f.focus();
      else if (lastEdit){ var ch = $('acct').querySelector('[data-edit="' + lastEdit + '"]'); lastEdit = null; if (ch) ch.focus(); }
    }
    function paintHistory(force){
      var rows = (S && Array.isArray(S.today)) ? S.today : [];
      var sig = rows.map(function(r){ return r.id; }).join(',');
      if (!force && sig === histSig) return;
      histSig = sig;
      var hist = $('hist'), top = hist.scrollTop;
      $('hist-n').textContent = rows.length + ' result' + (rows.length === 1 ? '' : 's');
      if (!rows.length) fill(hist, h('p', {class: 'hist-empty', text: 'Nothing closed yet today.'}));
      else fill(hist, ...rows.map(function(r){
        var l = laneOf(r.lane), bare = !r.gmail || r.gmail === '✗';
        return h('div', {class: 'hr'}, h('span', {class: 't tab', text: r.hm || ''}),
          h('span', {class: 'ap ' + l, role: 'img', 'aria-label': W(l)}, icon('logo-' + l)),
          h('span', {class: 's tab', text: String(r.serial || '')}),
          h('span', {class: 'r ' + (VICON[r.v] ? r.v : 'failed'), text: WORD[r.v] || String(r.v || '')}),
          h('span', {class: 'g' + (bare ? ' soft' : ''), text: bare ? 'no Google account' : r.gmail}),
          h('span', {class: 'x', text: r.exit || '—'}));
      }));
      hist.scrollTop = top;
    }
    function hint(form, text){ var hn = form.querySelector('.hint'); hn.textContent = text; hn.classList.add('bad'); return false; }
    function field(form, n){ var el = form.querySelector('[name="' + n + '"]'); return el ? el.value || '' : ''; }
    function saved(form, k, a){
      if (!a.ok){ hint(form, a.note || BROKE); return; }
      if (a.me && S) S.me = a.me;
      editing = null; lastEdit = k;
      paintMe(); renderAccount(false);
      say(a.note, 'ok');
      reloadIfSettled();
    }
    function profileSubmit(form){
      var k = form.getAttribute('data-form'), a = field(form, 'a').trim();
      if (form._busy) return;
      if (k === 'name'){
        if (!a) return hint(form, 'Your name cannot be empty.');
        form._busy = true;
        send('/station/me/name', {name: a}).then(function(r){ form._busy = false; saved(form, k, r); });
      } else if (k === 'user'){
        a = a.toLowerCase();
        if (!/^[a-z0-9._]{3,20}$/.test(a)) return hint(form, '3 to 20 small letters, digits, dots or underscores.');
        if (!/^[a-z0-9]/.test(a)) return hint(form, 'Start it with a letter or a digit.');
        form._busy = true;
        send('/station/me/username', {username: a}).then(function(r){ form._busy = false; saved(form, k, r); });
      } else {
        var cur = field(form, 'a'), b = field(form, 'b'), c = field(form, 'c');
        if (!cur) return hint(form, 'Type your current password first.');
        if (b.length < 8) return hint(form, 'The new password needs at least 8 characters.');
        if (b !== c) return hint(form, 'The two new passwords are not the same.');
        if (b === cur) return hint(form, 'The new password is the same as the current one.');
        form._busy = true;
        send('/station/me/password', {current: cur, password: b, again: c}).then(function(r){
          form._busy = false;
          form.querySelectorAll('input').forEach(function(i){ i.value = ''; });
          saved(form, k, r);
        });
      }
    }
    function profileClick(t){
      var ed = t.closest('[data-edit]');
      if (ed){ editing = lastEdit = ed.getAttribute('data-edit'); renderAccount(false); return true; }
      if (t.closest('[data-edit-cancel]')){ editing = null; renderAccount(false); reloadIfSettled(); return true; }
      return !!t.closest('#profile');
    }

    // ------------------------------------------------ the hash from a tab
    // "Back to station" in a Live tab moves this page's hash to #p-<serial>:
    // the card it names is lit, scrolled to and its Boot focused.
    function onHash(){
      var m = /^#p-(\d+)$/.exec(location.hash || '');
      if (!m) return;
      history.replaceState(null, '', location.pathname);
      if (!$('scrim').hidden) return;
      showProfile(false);
      var c = cards[m[1]];
      if (!c || !c.el || c.closing) return;
      setActive(c);
      try { c.el.scrollIntoView({block: 'nearest'}); } catch (e) { /* no scroll */ }
      // Boot is disabled while the phone boots or changes IP, and a disabled
      // button takes no focus: then the card's first one that can.
      focusQuiet(c.el.querySelector('.open:not([disabled])') || c.el.querySelector('button:not([disabled])'));
    }

    // ------------------------------------------------------- listeners
    document.addEventListener('click', function(e){
      var t = e.target;
      if (!t || !t.closest) return;
      if (armed && !armed.b.contains(t)) disarm();
      if (t.closest('[data-profile]')){ showProfile(true); return; }
      if (t.closest('[data-station]')){ showProfile(false); focusQuiet(document.querySelector('.who .me')); return; }
      if (onProfile && !t.closest('.bar,#scrim') && profileClick(t)) return;
      var tk = t.closest('[data-take]');
      if (tk){
        if (e.detail > 1 || tk._busy || tk.disabled) return;
        tk._busy = true; setTimeout(function(){ tk._busy = false; }, 700);
        showProfile(false); take(tk.getAttribute('data-take'));
        return;
      }
      if (t.closest('[data-build]')){ openBuild(); return; }
      if (t.id === 'scrim'){ if (downOnScrim) closeBuild(); return; }
      if (t.closest('[data-close]')){ closeBuild(); return; }
      var pk = t.closest('[data-pick]');
      if (pk){ pickLane(pk.getAttribute('data-pick')); return; }
      var md = t.closest('.dlg [data-m]');
      if (md){ setMode(md.closest('[data-row]').getAttribute('data-row'), md.getAttribute('data-m'), true); return; }
      if (t.closest('.dlg')) return;
      var b = t.closest('[data-a]'), a = b ? b.getAttribute('data-a') : '';
      if (a === 'unbuild' || a === 'dismiss'){
        disarm();
        if (!b._busy){ b._busy = true; setTimeout(function(){ b._busy = false; }, 700);
          (a === 'unbuild' ? callOff : dismiss)(b.closest('.ghost'), b.getAttribute('data-id')); }
        return;
      }
      if (a === 'unqueue'){
        disarm();
        if (!b._busy){ b._busy = true; setTimeout(function(){ b._busy = false; }, 700); leaveLine(b.getAttribute('data-l')); }
        return;
      }
      var c = cardOf(t);
      if (!c || c.closing){ disarm(); return; }
      setActive(c);
      if (b) act(c, a, b);
    });
    document.addEventListener('submit', function(e){
      var f = e.target;
      if (!f || !f.closest) return;
      if (f.classList.contains('boot')){ bootSubmit(e, f); return; }
      if (f.id === 'dlg'){ e.preventDefault(); startBuild(); return; }
      if (f.closest('#profile') && f.getAttribute('data-form')){ e.preventDefault(); profileSubmit(f); }
    });
    document.addEventListener('input', function(e){
      var i = e.target && e.target.closest && e.target.closest('.dlg .typed');
      if (i) check(i.closest('[data-row]').getAttribute('data-row'));
    });
    $('scrim').addEventListener('pointerdown', function(e){ downOnScrim = e.target && e.target.id === 'scrim'; });
    document.addEventListener('keydown', function(e){
      var t = e.target || {};
      if (!$('scrim').hidden){
        if (e.key === 'Escape') closeBuild();
        else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && t.closest && t.closest('.kinds')){
          var ks = ['gpt', 'spotify', 'other'], nx = ks[(ks.indexOf(buildLane) + (e.key === 'ArrowRight' ? 1 : 2)) % 3];
          pickLane(nx); focusQuiet(document.querySelector('.dlg [data-pick="' + nx + '"]')); e.preventDefault();
        } else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && t.closest && t.closest('.seg2')){
          var sg = t.closest('.seg2'), bs = [].slice.call(sg.querySelectorAll('[data-m]'));
          var rk = sg.closest('[data-row]').getAttribute('data-row');
          var at = bs.map(function(x){ return x.getAttribute('data-m'); }).indexOf(mode[rk]);
          var nb = bs[(at + (e.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length];
          setMode(rk, nb.getAttribute('data-m')); focusQuiet(nb); e.preventDefault();
        }
        return;
      }
      if (onProfile){
        if (e.key === 'Escape' && editing){ editing = null; renderAccount(false); reloadIfSettled(); }
        return;
      }
      var st = t.closest && t.closest('.step');
      if (st && (e.key === 'Enter' || e.key === ' ')){
        e.preventDefault();
        var c = cardOf(st);
        if (c && !c.closing && st.getAttribute('data-w')){ disarm(); setActive(c); copyCard(c, st); }
      }
    });
    // A moment plays once. A card that has risen stays risen when the station is
    // shown again; a lit chip, a bumped count or a fresh code goes back to rest.
    document.addEventListener('animationend', function(e){
      var t = e.target, n = e.animationName, c = t && t.classList;
      if (!c) return;
      if (n === 'rise' && c.contains('col') && !c.contains('arrive')) c.add('settled');
      else if (n === 'bloom'){ c.remove('arrive'); c.add('settled'); }
      else if (n === 'bump' || n === 'roll') c.remove(n);
      else if (n === 'moved' && t.parentNode) t.parentNode.classList.remove('new');
    });
    document.addEventListener('visibilitychange', function(){ if (document.visibilityState === 'visible') schedulePull(); });
    addEventListener('hashchange', onHash);
    // The bar folds to one slim line once the page scrolls: past 64px, and it
    // opens again above 8px, so the height it gives back cannot bounce the page.
    var barEl = $('bar');
    addEventListener('scroll', function(){
      var y = scrollY;
      if (y > 64) barEl.classList.add('tight'); else if (y < 8) barEl.classList.remove('tight');
    }, {passive: true});

    var st0 = readState();
    if (st0) apply(st0, 'first');
    else { first = false; schedulePull(); }
    onHash();
    listen();
    setInterval(tick1s, 1000);
  }

  // ===================================================== the Live tab
  function liveTab(){
    var st0 = readState() || {};
    var s = document.body.getAttribute('data-serial') || String(st0.serial || '');
    // The tab's own name, however it was opened (a pasted address, a restored
    // tab): Boot and the Station's openTab then find it instead of opening a
    // second tab that beats the same phone.
    if (s && window.name !== 'gf-live-' + s) window.name = 'gf-live-' + s;
    var L = null, conn = '', noteNow = '', screenSig = '';
    var live = $('live'), side = $('lt-side'), frame = $('lt-view'), screen = $('lt-screen'), tools = $('lt-tools');
    var stage = $('lt-stage'), col = $('lt-col'), ltBar = $('lt-bar'), box = $('lt-box');
    var V = st0.viewer || {};
    var VW = num(V.w, 360), BOX_W = num(V.box_w, 416), BOX_H = num(V.box_h, 752), BAR = num(V.bar, 32);
    var frameBase = '', frameLoaded = false, connNote = '', onNote = '';
    var pend = null, exitShown, sideSig = null, busySince = 0;
    var beatT = null, pollT = null, clockT = null, rebeatT = null, pressBusy = false;
    // Signed-out answers in a row, a beat's and a poll's apart: one alone may
    // be a blip, two mean it.
    var beatStrikes = 0, pollStrikes = 0;
    var SIGNED_OUT = 'signed out – sign in again in the station';

    function may(k){ return !!(L && L.may && L.may[k]); }
    function q(sel){ return side.querySelector(sel); }
    var CONN = {booting: ['Booting', 'booting – the screen opens as soon as the phone is on'],
      connecting: ['Connecting', 'connecting – the screen is on its way'],
      on: ['On', 'watching – it stays yours while this tab is open; closing it switches the phone off'],
      changing: ['Changing IP', 'changing IP – the phone stops, moves to the next free IP and starts again; about a minute'],
      off: ['Ready', 'ready – the phone is off; Boot again switches it on'],
      released: ['Released', 'released – this phone is no longer yours']};

    // ------------------------------------------------ framing the viewer
    var stacked = window.matchMedia ? matchMedia('(max-width:900px)') : {matches: false};
    function fit(){
      var hh = stage.clientHeight || innerHeight, ww = stage.clientWidth || innerWidth;
      var k = stacked.matches ? Math.min(1, (ww - 56) / BOX_W)
        : Math.min((hh - 40) / BOX_H, (ww - 56) / BOX_W, 960 / BOX_H);
      if (!(k > 0)) k = 0.1;
      col.style.width = Math.floor(BOX_W * k) + 'px';
      ltBar.style.height = Math.round(BAR * k) + 'px';
      box.style.height = Math.floor((BOX_H - BAR) * k) + 'px';
      frame.style.width = BOX_W + 'px'; frame.style.height = BOX_H + 'px';
      frame.style.transform = 'translateY(' + (-BAR * k) + 'px) scale(' + k + ')';
    }
    function viewerUrl(url){
      try {
        var u = new URL(url);
        if (u.protocol !== 'https:') return '';
        u.searchParams.set('w', String(VW));
        return u.href;
      } catch (e) { return ''; }
    }
    function loadFrame(url){
      var src = viewerUrl(url);
      frameBase = url; frameLoaded = false;
      frame.hidden = false;
      if (src) frame.setAttribute('src', src);
    }
    function blankFrame(){
      var cur = frame.getAttribute('src');
      if (cur && cur !== 'about:blank') frame.setAttribute('src', 'about:blank');
      frame.hidden = true; frameBase = ''; frameLoaded = false;
    }
    frame.addEventListener('load', function(){
      var cur = frame.getAttribute('src');
      if (!cur || cur === 'about:blank' || !frameBase) return;
      frameLoaded = true;
      if (conn === 'connecting'){ var n = onNote; onNote = ''; setConn('on', n); }
    });

    // ------------------------------------------------------ the margin
    function buildSide(d){
      var l = laneOf(d.lane), a = d.acct, gone = d.conn === 'released';
      var parts = [
        h('button', {type: 'button', class: 'p-back lt-back', 'data-lt-a': 'station'}, h('span', {class: 'ic'}, icon('arrow')), 'Back to station'),
        h('div', {class: 'hd'},
          h('span', {class: 'app ' + l, role: 'img', 'aria-label': W(l) + ' phone'}, icon('logo-' + l)),
          h('span', {class: 'meta'}, h('span', {class: 's tab', text: s}), h('span', {class: 'st', 'data-lt': 'st'})),
          h('span', {class: 'right'},
            h('span', {class: 'ip', 'data-lt': 'ip'}, h('small', {text: 'IP'}), h('span', {'data-lt': 'exit'})),
            h('span', {class: 'age'}, icon('clock'), h('span', {class: 'tab', 'data-lt': 'age'})))),
        h('p', {class: 'lt-note', 'data-lt': 'note'}),
        h('div', {class: 'lt-doors', role: 'group', 'aria-label': 'More for this phone'},
          h('button', {type: 'button', 'data-lt-a': 'reload'}, icon('reload'), h('span', {text: 'Reload'})),
          h('button', {type: 'button', 'data-lt-a': 'ip'}, icon('globe'), h('span', {text: 'Change IP'})),
          h('button', {type: 'button', 'data-lt-a': 'back'}, icon('back'), h('span', {text: 'Give back'}))),
        h('button', {type: 'button', class: 'open lt-boot', 'data-lt-a': 'boot', hidden: true}, icon('power'), h('span', {text: 'Boot again'}))];
      // A tab that opens on a phone no longer theirs has nothing of it to show.
      if (!gone) parts.push(h('p', {class: 'lt-h', text: 'On this phone'}), h('div', {class: 'steps'}, ...gmailSteps(d, true)));
      if (!gone && a && a.address){
        var acct = [step('acct', 'Address', a.address, true)];
        if (a.pw) acct.push(step('apw', 'Password', a.pw, true));
        if (a.totp) acct.push(step('acode', 'Code', '', true));
        parts.push(h('p', {class: 'lt-h'}, a.title || 'App account', a.kind ? h('span', {class: 'lt-kind', text: a.kind}) : null),
          h('div', {class: 'steps'}, ...acct));
      }
      parts.push(verdictRow(true));
      fill(side, ...parts);
      exitShown = undefined;
    }
    function sigOf(d){
      var a = d.acct || {};
      return [d.bare ? 1 : 0, d.gmail || '', d.pw || '', d.totp ? 1 : 0, a.title || '', a.kind || '',
        a.address || '', a.pw || '', a.totp ? 1 : 0].join('\u0001');
    }
    function paintFacts(){
      if (!L) return;
      if (conn !== 'released' && L.conn !== 'released'){
        var sig = sigOf(L);
        if (sig !== sideSig){ sideSig = sig; buildSide(L); if (conn) setConn(conn, noteNow, true); }
      }
      var ex = L.exit || '—', ip = q('[data-lt="ip"]'), exEl = q('[data-lt="exit"]');
      if (!ip || !exEl) return;
      if (exitShown !== undefined && exitShown !== ex){ ip.classList.remove('new'); void ip.offsetWidth; ip.classList.add('new'); }
      exitShown = ex; exEl.textContent = ex;
      clock();
    }
    function waitScreen(c){
      var ex = (L && L.exit) || '—';
      var t = {booting: ['Booting phone ' + s, 'Its screen opens here as soon as it is on.'],
        connecting: ['Connecting to phone ' + s, 'The screen appears in a moment.'],
        changing: ['Changing the IP of ' + s, 'The phone stops, moves to the next free IP and starts again.'],
        off: [s + ' is ready to boot', 'It is switched off, on ' + ex + '. Press Boot again to switch it on.'],
        released: ['This phone was released', 'It is no longer yours. You can close this tab.']}[c];
      var sig = c + '|' + ex;
      if (sig === screenSig) return;
      screenSig = sig;
      var spin = c === 'booting' || c === 'connecting' || c === 'changing';
      fill(screen, h('div', {class: 'ph-wait ' + c},
        spin ? h('span', {class: 'spin'}) : h('span', {class: 'pw'}, icon('power')),
        h('b', {text: t[0]}), h('span', {text: t[1]}),
        c === 'released' ? h('button', {type: 'button', class: 'open', 'data-lt-a': 'station'}, icon('arrow'), h('span', {text: 'Back to station'})) : null));
    }
    // What the tab shows. Released is final: nothing brings the tab back after it.
    function setConn(c, note, force){
      if (conn === 'released' && c !== 'released') return;
      var changed = c !== conn;
      conn = c;
      if (changed || note || force) noteNow = note || CONN[c][1];
      side.dataset.state = c;
      live.dataset.power = c === 'released' ? 'gone' : c === 'off' ? 'ready'
        : (c === 'booting' || c === 'changing') ? 'busy' : 'on';
      var stEl = q('[data-lt="st"]'), noteEl = q('[data-lt="note"]'), boot = q('[data-lt-a="boot"]');
      if (stEl) stEl.textContent = CONN[c][0];
      if (noteEl) noteEl.textContent = noteNow;
      if (boot) boot.hidden = c !== 'off';
      side.querySelectorAll('.lt-doors button').forEach(function(b){
        var d = b.getAttribute('data-lt-a');
        b.disabled = c === 'released' || (d !== 'back' && (c === 'booting' || c === 'changing')) ||
          (c === 'off' && d === 'reload') || (d === 'ip' && !may('ip')) || (d === 'back' && !may('take'));
      });
      side.querySelectorAll('.verdict button').forEach(function(b){ b.disabled = c === 'released' || !may('take'); });
      if (c === 'on' || (c === 'connecting' && frameBase)){
        frame.hidden = false; tools.hidden = true;
        if (c === 'on'){ screen.hidden = true; screenSig = ''; }
        else {
          screen.hidden = false;
          screen.style.position = 'absolute'; screen.style.top = '0'; screen.style.left = '0';
          screen.style.right = '0'; screen.style.bottom = '0'; screen.style.zIndex = '1';
          waitScreen(c);
        }
      } else {
        if (c !== 'connecting') blankFrame();
        screen.hidden = false; tools.hidden = false;
        screen.style.position = ''; screen.style.top = ''; screen.style.left = '';
        screen.style.right = ''; screen.style.bottom = ''; screen.style.zIndex = '';
        waitScreen(c);
      }
      if (c === 'released') stopAll();
    }
    function stopAll(){
      clearInterval(beatT); clearTimeout(pollT); clearInterval(clockT); clearTimeout(rebeatT);
      beatT = pollT = clockT = rebeatT = null;
      disarm();
    }
    function clock(){
      if (!L || conn === 'released') return;
      var held = Math.floor((now() - num(L.taken_at, now())) / MIN);
      var ag = q('[data-lt="age"]');
      if (ag) ag.textContent = held < 1 ? 'just now' : held + ' min';
      var sec = codeSec();
      [['code', 'timer', L.totp], ['acode', 'atimer', L.acct && L.acct.totp]].forEach(function(x){
        var el = q('[data-lt-k="' + x[0] + '"]');
        if (!el || !x[2]) return;
        var text = codeNow(x[2], clock);
        if (text) setCode(el, text);
        var tm = q('[data-lt-k="' + x[1] + '"]');
        if (tm) tm.style.transform = 'scaleX(' + (sec / 30) + ')';
        var line = q('[data-lt-copy="' + x[0] + '"]');
        if (line) line.classList.toggle('late', sec <= 5);
      });
    }

    // ------------------------------------------------ applying a state
    function applyLive(st){
      if (!st || typeof st !== 'object' || !st.conn) return;
      if (typeof st.now === 'number') skew = st.now - Date.now();
      L = st;
      if (st.viewer){
        VW = num(st.viewer.w, VW); BOX_W = num(st.viewer.box_w, BOX_W);
        BOX_H = num(st.viewer.box_h, BOX_H); BAR = num(st.viewer.bar, BAR);
      }
      if (st.conn === 'released'){
        paintFacts();
        setConn('released', st.why === 'closed' ? 'closed – ' + s + ' is being deleted' : '');
        return;
      }
      paintFacts();
      var last = st.last;
      if (pend && pend.id && last && last.id >= pend.id){
        var p = pend;
        pend = null;
        if (!last.ok) say(last.note || 'That did not go through.', true);
        else if (p.kind === 'ip'){
          var l = laneOf(st.lane);
          if (last.started){
            say(s + ' moved from ' + last.was + ' to ' + last.now + '.', l);
            connNote = 'on a new IP now – the screen is coming back';
            onNote = 'watching – on a new IP now; closing this tab switches the phone off';
          } else if (p.then === 'off'){
            say(s + ' moved from ' + last.was + ' to ' + last.now + '. It stays ready to boot.', l);
            if (st.conn === 'off') setConn('off', s + ' is on ' + last.now + ' now – press Boot again');
          } else {
            say(s + ' moved from ' + last.was + ' to ' + last.now + ' but did not start.', true);
            if (st.conn === 'off') setConn('off', s + ' is on ' + last.now + ' now but did not start – press Boot again');
          }
        }
      }
      if (pend && pend.until < Date.now()) pend = null;
      follow(st);
    }
    function follow(st){
      var c = st.conn;
      if (c === 'on' && st.url){
        if (frameBase !== st.url){
          loadFrame(st.url);
          var n = connNote; connNote = '';
          setConn('connecting', n);
        } else if (frameLoaded && conn !== 'on'){ var m = onNote; onNote = ''; setConn('on', m); }
        else if (!frameLoaded && conn !== 'connecting') setConn('connecting', connNote);
        return;
      }
      if (c === 'on') c = 'off';
      if (pend && !pend.id) return;       // the press has not been answered yet
      if (c !== conn) setConn(c);
    }

    // ------------------------------------------------------- the presses
    function sendLive(path, fields){
      pressing++;
      var turn = ++seq;
      pressBusy = true;
      return api(path, fields).then(function(a){
        pressing--; pressBusy = false;
        return a;
      }).then(function(a){
        return {a: a, turn: turn};
      });
    }
    function settle(r, pre){
      var a = r.a;
      try { if (pre) pre(a); } catch (e) { report(e, 'press'); }
      if (a && a.live){
        lastPressApplied = Math.max(lastPressApplied, r.turn);
        try { applyLive(a.live); } catch (e) { report(e, 'apply'); }
        schedulePoll(1500);
      } else schedulePoll(0);
      return a;
    }
    function bootAgain(){
      pend = {kind: 'boot', id: 0, until: Date.now() + 200000};
      setConn('booting', 'booting ' + s + ' again – it stays yours');
      sendLive('/phones/' + s + '/boot', {station: 1}).then(function(r){
        return settle(r, function(a){
          if (a.ok && a.req && pend) pend.id = a.req; else pend = null;
        });
      }).then(function(a){ if (!a.ok){ say(a.note || BROKE, true); if (!a.live) setConn('off'); } });
    }
    function changeIp(){
      var then = conn === 'on' || conn === 'connecting' ? 'on' : 'off';
      pend = {kind: 'ip', id: 0, then: then, until: Date.now() + 200000};
      setConn('changing');
      sendLive('/phones/' + s + '/proxy', {station: 1, keep_power: 1, was: then}).then(function(r){
        return settle(r, function(a){
          if (a.ok && a.req && pend) pend.id = a.req; else pend = null;
        });
      }).then(function(a){ if (!a.ok){ say(a.note || BROKE, true); if (!a.live) setConn(then === 'on' ? 'connecting' : 'off'); } });
    }
    function giveBack(){
      sendLive('/station/phones/' + s + '/back', {where: 'station-live'}).then(function(r){ return settle(r); }).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); return; }
        setConn('released');
        say('Phone ' + s + ' is back on ' + home(laneOf(L ? L.lane : '')) + '.', laneOf(L ? L.lane : ''));
        toStation(s);
      });
    }
    function verdict(b){
      if (conn === 'released' || pressBusy || b.disabled) return;
      if (!tap(b)) return;
      var v = b.getAttribute('data-v');
      sendLive('/phones/' + s + '/state', {state: v, sure: 1, where: 'station-live'}).then(function(r){ return settle(r); }).then(function(a){
        if (!a.ok){ say(a.note || BROKE, true); return; }
        setConn('released', 'closed as ' + WORD[v] + ' – it is deleted in a moment');
        say('Phone ' + s + ' closed as ' + WORD[v] + '. It is deleted in a moment.', v);
        toStation(s);
      });
    }
    function reload(){
      if (conn !== 'on' && conn !== 'connecting') return;
      var url = frameBase;
      frame.setAttribute('src', 'about:blank'); frameLoaded = false;
      setConn('connecting', 'loading the screen again');
      setTimeout(function(){ if (conn === 'connecting' && frameBase === url) loadFrame(url); }, 300);
    }
    // Back to the Station's own tab, wherever it lives, without reloading it
    // and without leaving this one. Whether that tab holds a Station is read
    // from its document, not its path: a tab named gf-station that went back
    // to the dashboard at '/' is not one, and a tab on another site cannot be
    // read, so both are sent to /station.
    function toStation(serial){
      var w = window.open('', 'gf-station');
      if (!w) return;
      var isSt = false;
      try {
        isSt = !!(w.document && w.document.body && w.document.body.getAttribute('data-page') === 'station');
      } catch (e) { isSt = false; }
      if (!isSt) w.location.href = '/station#p-' + serial;
      else w.location.hash = 'p-' + serial;
      w.focus();
    }
    function liveCopy(st){
      var k = st.getAttribute('data-lt-copy'), a = (L && L.acct) || {};
      var text = k === 'code' || k === 'acode'
        ? (q('[data-lt-k="' + k + '"]').textContent || '').replace(' ', '')
        : {gmail: L.gmail, pw: L.pw, acct: a.address, apw: a.pw}[k];
      copyStep(st, text || '', k, s, laneOf(L.lane));
    }

    // ------------------------------------------------ beats and polls
    // A 503, a network error or a beat that timed out says nothing about the
    // phone. A signed-out answer is asked once more, three seconds on, before
    // the tab lets the phone go.
    function beat(){
      if (conn === 'released') return;
      clearTimeout(rebeatT); rebeatT = null;
      api('/phones/' + s + '/watching', {station: 1}, {raw: true, redirect: 'manual', keepalive: true, noPress: true}).then(function(r){
        if (!r || conn === 'released') return;
        if (r.status === 200){ beatStrikes = 0; return; }
        if (r.status === 410){ setConn('released'); return; }
        if (r.type === 'opaqueredirect' || r.status === 0 || r.status === 401 || r.status === 403){
          if (++beatStrikes >= 2) setConn('released', SIGNED_OUT);
          else rebeatT = setTimeout(beat, 3000);
        }
      });
    }
    function nextPoll(){
      var busy = conn === 'booting' || conn === 'changing';
      if (!busy){ busySince = 0; return 15000; }
      if (!busySince) busySince = Date.now();
      return Date.now() - busySince < (conn === 'changing' ? 180000 : 120000) ? 1500 : 15000;
    }
    function schedulePoll(ms){
      clearTimeout(pollT); pollT = null;
      if (conn === 'released') return;
      pollT = setTimeout(poll, ms === undefined ? nextPoll() : ms);
    }
    function poll(){
      pollT = null;
      if (conn === 'released') return;
      if (pressing){ schedulePoll(); return; }
      var turn = ++seq;
      api('/station/phones/' + s + '/state', null, {method: 'GET', raw: true}).then(function(r){
        if (!r) return null;
        if (r.status === 401 || r.status === 403){
          if (++pollStrikes >= 2) setConn('released', SIGNED_OUT);
          else { schedulePoll(3000); return 'again'; }
          return null;
        }
        if (r.status !== 200) return null;
        pollStrikes = 0;
        return readJSON(r);
      }).then(function(st){
        if (st === 'again') return;
        if (st && st.conn && !pressing && turn > lastPressApplied) applyLive(st);
        schedulePoll();
      }, function(err){ report(err, 'poll'); schedulePoll(); });
    }

    // ------------------------------------------------------- listeners
    document.addEventListener('click', function(e){
      var t = e.target;
      if (!t || !t.closest) return;
      if (armed && !armed.b.contains(t)) disarm();
      var v = t.closest('[data-lt-v]');
      if (v){ verdict(v); return; }
      disarm();
      var cp = t.closest('[data-lt-copy]');
      if (cp){ if (conn !== 'released') liveCopy(cp); return; }
      var b = t.closest('[data-lt-a]');
      if (!b || b.disabled) return;
      var w = b.getAttribute('data-lt-a');
      if (w === 'station') toStation(s);
      else if (w === 'reload') reload();
      else if (w === 'back'){ if (!pressBusy && conn !== 'released') giveBack(); }
      else if (w === 'boot'){ if (!pressBusy && conn === 'off') bootAgain(); }
      else if (w === 'ip'){ if (!pressBusy && (conn === 'on' || conn === 'off' || conn === 'connecting')) changeIp(); }
    });
    document.addEventListener('keydown', function(e){
      var t = e.target, ls = t && t.closest && t.closest('[data-lt-copy]');
      if (ls && (e.key === 'Enter' || e.key === ' ')){
        e.preventDefault(); disarm();
        if (conn !== 'released') liveCopy(ls);
      }
    });
    document.addEventListener('animationend', function(e){
      var t = e.target, n = e.animationName, c = t && t.classList;
      if (!c) return;
      if (n === 'roll') c.remove(n);
      else if (n === 'moved' && t.parentNode) t.parentNode.classList.remove('new');
    });
    document.addEventListener('visibilitychange', function(){
      if (document.visibilityState === 'visible'){ beat(); schedulePoll(0); }
    });
    addEventListener('pagehide', function(){
      if (conn === 'released') return;
      try {
        var body = new URLSearchParams({csrf: csrf(), station: '1'}).toString();
        navigator.sendBeacon('/phones/' + s + '/closing', new Blob([body], {type: 'application/x-www-form-urlencoded'}));
      } catch (e) { report(e, 'beacon'); }
    });
    addEventListener('resize', fit);
    if (stacked.addEventListener) stacked.addEventListener('change', fit);

    fit();
    L = st0;
    sideSig = sigOf(st0);
    buildSide(st0);
    // A Boot pressed on the Station's card arrives here as its queued press:
    // held as this tab's own, so a boot the keeper fails later says why.
    var arr0 = st0.arrival;
    if (arr0 && arr0.req && (arr0.said === 'queued' || arr0.said === 'twice' || arr0.said === 'already'))
      pend = {kind: 'boot', id: arr0.req, until: Date.now() + 200000};
    applyLive(st0);
    if (!conn) setConn('off');
    var arrival = st0.arrival;
    if (arrival){
      if (NO_WORDS[arrival.said] && arrival.note) say(arrival.note, true);
      history.replaceState(null, '', '/station/phones/' + s);
    }
    focusQuiet(side);
    if (conn !== 'released'){
      beat();
      beatT = setInterval(beat, BEAT_MS);
      clockT = setInterval(clock, 1000);
      schedulePoll();
    }
  }

  if (PAGE === 'station') station();
  else if (PAGE === 'live') liveTab();
})();
