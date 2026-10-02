/* The Proxies page (2026-10-02): the prototype the user called final, on the
   live pool. The first state rides in the page's JSON island and every later
   one comes from /pools/proxy/state: each proxy with its state, lane, daily
   count and cap, where its address comes out, what operators pressed on its
   phones (today, 3 days, all time), and every use it has had. A press is a
   request the farm carries out - at once when it needs no word from the
   cloud, through its queue when it must test first (turning a proxy on, Test,
   Add) - and the page watches those to their end. */
let DATA = JSON.parse(document.getElementById("gf-state").textContent);

const LABEL = ["Done", "Decline", "OR", "Auth", "Failed"];
const K = ["d", "x", "o", "a", "f"];
const RANGES = [["t", "Today"], ["d", "3 days"], ["a", "All"]];
const WORD = {done: "Done", decline: "Decline", or: "OR", auth: "Auth", failed: "Failed"};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const view = {range: "a", axis: "day", scope: "all", lane: "", fam: "", q: "", sort: "name", dir: 1, flag: ""};
const ticked = new Set();
let menu = "", noteTimer = 0, menuFrom = null, drawerFrom = null;
/* What the last action touched: its rows and card glow once, a switch it
   turned on glows once. Each is read by the next draw and then emptied. */
const flashIds = new Set(), litIds = new Set();
/* Removed proxies go to the archive, and a batch's numbers are never given
   twice: the farm counts the archive's names too, and `issued` is the
   highest number each batch has given. Both come with the state. */
let archived = 0;
let issued = {};
let flashFam = "", litFam = "";
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
/* The pool as the farm last said, refilled in place by load(). */
const EX = [];

const el = id => document.getElementById(id);
const esc = t => String(t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const sum = a => a.reduce((x, y) => x + y, 0);
const pct = (a, b) => b ? Math.round(a / b * 100) : null;
const SVG = (d, w) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 2.1) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
const ICON = {
 tick: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', cross: '<path d="M7 7l10 10M17 7L7 17"/>',
 phone: '<rect x="6.5" y="2.5" width="11" height="19" rx="2.6"/><path d="M11 18.5h2"/>', plus: '<path d="M12 5v14M5 12h14"/>',
 copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
 lane: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M12 5v14"/>',
 cap: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 reload: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
 more: '<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
 trash: '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6 7l1 13h10l1-13"/>',
 chev: '<path d="M6 9l6 6 6-6"/>',
};
const LOGO = {
 gpt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M22.28 9.82a5.98 5.98 0 0 0-.51-4.91 6.05 6.05 0 0 0-6.51-2.9A6.07 6.07 0 0 0 4.98 4.18a5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .75 7.1 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9A5.98 5.98 0 0 0 13.26 24a6.06 6.06 0 0 0 5.77-4.21 5.99 5.99 0 0 0 4-2.9 6.06 6.06 0 0 0-.75-7.07zm-9.02 12.61a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.79.79 0 0 0 .39-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.49 4.5zM3.6 18.3a4.47 4.47 0 0 1-.54-3.01l.15.08 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.34a.08.08 0 0 1-.03.06L9.74 19.95a4.5 4.5 0 0 1-6.14-1.65zM2.34 7.9a4.49 4.49 0 0 1 2.37-1.98V11.6a.77.77 0 0 0 .39.68l5.81 3.35-2.02 1.17a.08.08 0 0 1-.07 0L4 14.02A4.5 4.5 0 0 1 2.34 7.87zm16.6 3.86L13.1 8.36l2.02-1.16a.08.08 0 0 1 .07 0l4.83 2.79a4.49 4.49 0 0 1-.68 8.1v-5.67a.79.79 0 0 0-.4-.67zm2.01-3.03l-.14-.08-4.78-2.78a.78.78 0 0 0-.78 0L9.41 9.23V6.9a.07.07 0 0 1 .03-.06l4.83-2.79a4.5 4.5 0 0 1 6.68 4.66zM8.31 12.86l-2.02-1.16a.08.08 0 0 1-.04-.06V6.07a4.5 4.5 0 0 1 7.38-3.45l-.14.08-4.78 2.76a.79.79 0 0 0-.4.68zm1.1-2.36l2.6-1.5 2.61 1.5v3l-2.6 1.5-2.6-1.5z"/></svg>',
 spotify: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.52 17.34c-.24.36-.66.48-1.02.24-2.82-1.74-6.36-2.1-10.56-1.14-.42.12-.78-.18-.9-.54-.12-.42.18-.78.54-.9 4.56-1.02 8.52-.6 11.64 1.32.42.18.48.66.3 1.02zm1.44-3.3c-.3.42-.84.6-1.26.3-3.24-1.98-8.16-2.58-11.94-1.38-.48.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.14C9.6 9.9 15 10.56 18.72 12.84c.36.18.54.78.24 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.3c-.6.18-1.2-.18-1.38-.72-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.72 1.62.54.3.72 1.02.42 1.56-.3.42-1.02.6-1.56.3z"/></svg>',
};

/* ---------------------------------------------------------------- helpers */
const vOf = e => e.v[view.range];
/* A batch added from this page is its seller, type and day; the pool's older
   proxies group by the letters their names start with, as they always have. */
const famKey = e => e.batch || e.f.toLowerCase();
const isOff = e => !!e.cc && e.cc !== "US";
/* A seller typed all small gets a capital (oxylab: Oxylab); a short type is
   in capitals (isp: ISP), a longer one capitalised (residential: Residential);
   anything typed with its own capitals is kept as typed (IProyal). */
const prettySeller = s => s && s === s.toLowerCase() ? s[0].toUpperCase() + s.slice(1) : s;
const prettyType = s => !s ? "" : s.length <= 3 ? s.toUpperCase() : s === s.toLowerCase() ? s[0].toUpperCase() + s.slice(1) : s;
/* A proxy's name to read: a batch proxy as "Oxylab ISP #1", any other as its key. */
const nameText = e => e.batch ? [e.seller, e.type].filter(Boolean).join(" ") + " #" + e.k : e.n;
const nameHtml = e => !e.batch ? esc(e.n) : '<span class="nm3"><span class="nl"><b>' + esc(e.seller) + '</b><i>#' + e.k + '</i></span><small>' +
  (e.type ? '<span class="ty2">' + esc(e.type) + '</span>' : '') + esc(stamp(e.added)) + '</small></span>';
const laneWord = l => !l ? "Either" : l === "gpt" ? "GPT" : "Spotify";
const tile = (l, sm) => !l ? '<span class="app either' + (sm ? " sm" : "") + '"><span class="pair">' + LOGO.gpt + LOGO.spotify + '</span></span>' : '<span class="app ' + l + (sm ? " sm" : "") + '">' + LOGO[l] + '</span>';
const stamp = s => { const m = /(\d{4})?-?(\d\d)-(\d\d)(?: (\d\d:\d\d))?/.exec(s || ""); return m ? (+m[3]) + " " + MONTHS[+m[2] - 1] + (m[4] ? " " + m[4] : "") : ""; };
/* In play: handed to builds - free, or on a phone it keeps. */
const inPlay = e => e.s === "free" || (e.s === "phone" && !e.after);
/* A proxy's switch: on while in play; off when set aside or dead; off but
   amber while still on a phone, until that phone goes. */
function psw(e) {
  const busy = pend.get(e.id) === "free", on = busy || inPlay(e), wait = !busy && e.s === "phone" && !!e.after;
  return '<button class="sw' + (wait ? ' wait' : '') + (busy ? ' busy' : '') + (litIds.has(e.id) ? ' lit' : '') + '" type="button" role="switch" aria-checked="' + on + '"' + (busy ? ' aria-busy="true"' : '') + ' data-psw="' + e.id + '" aria-label="' + esc(nameText(e)) + ' in play" title="' +
    (busy ? 'Being tested: back in play if it answers' : on ? 'In play: turn off to set it aside' : wait ? 'Set aside once its phone goes: turn on to keep it in play' : 'Set aside: turn on to test it and put it back in play') + '"><i></i></button>';
}
/* Its state as a pill, and under it what happens next: set aside once its
   phone goes, or the test the farm is running on it. */
function pill(e) {
  const p = pend.get(e.id);
  const then = p ? (p === "free" ? "testing, then in play" : "testing\u2026") : e.s === "phone" && e.after ? "then set aside" : "";
  const st = e.s === "free" ? '<span class="st free">Free</span>'
    : e.s === "phone" ? '<span class="st phone">' + (e.serial ? 'On <b>' + esc(e.serial) + '</b>' : 'On a phone') + '</span>'
    : e.s === "dead" ? '<span class="st dead">Dead</span>' : '<span class="st aside">Set aside</span>';
  return st + (then ? '<small class="then">' + then + '</small>' : '');
}
const mixBar = v => { const n = sum(v); return '<span class="mix" aria-hidden="true">' + K.map((k, i) => v[i] ? '<i class="' + k + '" style="width:' + (v[i] / n * 100) + '%"></i>' : "").join("") + '</span>'; };
function advice(v) {
  const n = sum(v);
  if (n < 8) return ["few", "Too few"];
  const d = v[0] / n, o = v[2] / n;
  if (d < 0.4 || o >= 0.4) return ["stop", "Stop"];
  if (d >= 0.65) return ["keep", "Keep"];
  return ["watch", "Watch"];
}
/* Show only: each rule in words, with its colour and why it is worth a
   look. Rates are the range's and need 4 pressed phones. */
const RANGE_WORD = {t: "today", d: "in the last 3 days", a: "so far"};
const rate = (e, i) => { const v = vOf(e), n = sum(v); return n ? v[i] / n : null; };
const FLAGS = [
  {k: "or", w: "OR 40%+", c: "o", test: e => sum(vOf(e)) >= 4 && rate(e, 2) >= 0.4,
   why: () => "4 or more of its phones were pressed " + RANGE_WORD[view.range] + ", and 40% or more of them ended in OR."},
  {k: "decline", w: "Decline 25%+", c: "x", test: e => sum(vOf(e)) >= 4 && rate(e, 1) >= 0.25,
   why: () => "4 or more of its phones were pressed " + RANGE_WORD[view.range] + ", and a quarter or more of them ended in Decline."},
  {k: "abroad", w: "Outside the US", c: "c", test: isOff,
   why: () => "Its address comes out in another country; the amber edge on its row says the same."},
  {k: "capped", w: "Capped today", c: "v", test: e => !!e.cap && e.today >= e.cap,
   why: () => "It has carried as many phones today as its daily cap allows, and waits for tomorrow."},
  {k: "fresh", w: "Never used", c: "n", test: e => !(e.u || []).length,
   why: () => "No phone has been built on it or moved onto it yet."},
  {k: "dead", w: "Dead", c: "r", rare: true, test: e => e.s === "dead",
   why: () => "Its last test failed; it stays out of the builds until it passes one."},
];
const FLAG = Object.fromEntries(FLAGS.map(f => [f.k, f]));
/* The orders the list can take, each said in words both ways; null sorts
   last either way, and a tie goes to the proxy with more pressed phones. */
const SORTS = {
  name: {w: ["Name, A to Z", "Name, Z to A"], dir: 1},
  or: {w: ["Most OR first", "Least OR first"], dir: -1, val: e => rate(e, 2)},
  decline: {w: ["Most Decline first", "Least Decline first"], dir: -1, val: e => rate(e, 1)},
  done: {w: ["Best Done first", "Worst Done first"], dir: -1, val: e => rate(e, 0)},
  phones: {w: ["Most phones first", "Fewest phones first"], dir: -1, val: e => sum(vOf(e))},
  today: {w: ["Busiest today first", "Quietest today first"], dir: -1, val: e => e.today},
  state: {w: ["On phones first", "Set aside first"], dir: 1, val: e => ({phone: 0, free: 1, aside: 2, dead: 3})[e.s]},
};
/* The menu holds the five orders that answer the page's questions; Today and
   State stay a click on their column heads away. */
const SORT_MENU = [["name"], ["or", "decline", "done", "phones"]];
const sortWord = (k, dir) => SORTS[k].w[dir === SORTS[k].dir ? 0 : 1];

/* ----------------------------------------------------------------- rounds */
/* A use is a phone given the proxy - built on it, whether Google let it in or
   not, or moved onto it with Change IP - and a proxy's k-th use is its round
   k. A batch's round is the median of its proxies' rounds. Presses were
   first recorded on 29 Sep, so earlier uses count for sign-ins only.
   Each use in the data: [when, 1 signed in | 0 refused | 2 moved onto it,
   press d x o a f or "", 1 if the proxy came out at a new address, the
   phone, who pressed, when the use ended or "" while its phone held it]. */
let TRACK = "";
const BI = {d: 0, x: 1, o: 2, a: 3, f: 4}, BW = {d: "done", x: "decline", o: "or", a: "auth", f: "failed"};
const SINCE = {t: "", d: "", a: ""};
function roundsOf(exits) {
  const R = [];
  exits.forEach(e => (e.u || []).forEach(([when, how, b, ip], i, us) => {
    const r = R[i] || (R[i] = {k: i + 1, uses: 0, builds: 0, signed: 0, v: [0, 0, 0, 0, 0], tracked: 0, first: when, last: when, ips: 0, rests: [], holds: []});
    const rest = restBefore(us, i), hold = holdOf(us[i]);
    if (rest != null) r.rests.push(rest);
    if (hold != null) r.holds.push(hold);
    r.uses++;
    if (how !== 2) { r.builds++; if (how === 1) r.signed++; }
    if (b) r.v[BI[b]]++;
    if (when >= TRACK) r.tracked++;
    if (when < r.first) r.first = when;
    if (when > r.last) r.last = when;
    r.ips += ip;
  }));
  return R.filter(Boolean);
}
/* Rest and hold. A use's 7th field is when it ended - its phone moved on to
   another proxy, or closed - and "" while the phone still held it. Hold: how
   long a phone kept the proxy (a refused sign-in's phone goes at once and
   holds nothing). Rest: how long the proxy then sat idle, from the previous
   phone leaving to the next one coming. Times are Tehran "YYYY-MM-DD HH:MM",
   subtracted as if UTC - exact, since Tehran keeps no summer time. */
const tOf = w => w ? Date.UTC(+w.slice(0, 4), +w.slice(5, 7) - 1, +w.slice(8, 10), +w.slice(11, 13), +w.slice(14, 16)) : NaN;
const holdOf = u => u[1] !== 0 && u[6] ? (tOf(u[6]) - tOf(u[0])) / 36e5 : null;
const restBefore = (us, i) => i && us[i - 1][6] ? Math.max(0, (tOf(us[i][0]) - tOf(us[i - 1][6])) / 36e5) : null;
const holdsOf = e => (e.u || []).map(holdOf);
const restsOf = e => (e.u || []).map((u, i, us) => restBefore(us, i));
/* A working day runs noon to noon, Tehran: the farm works through the night,
   and midnight would cut one night's work in two. Named by the day it starts. */
const wday = w => new Date(tOf(w) - 12 * 36e5).toISOString().slice(0, 10);
const wdayName = k => { const d = new Date(Date.UTC(+k.slice(0, 4), +k.slice(5, 7) - 1, +k.slice(8, 10))), n = new Date(d.getTime() + 864e5);
  return d.getUTCMonth() === n.getUTCMonth() ? d.getUTCDate() + "\u2013" + n.getUTCDate() + " " + MONTHS[n.getUTCMonth()] : d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " \u2013 " + n.getUTCDate() + " " + MONTHS[n.getUTCMonth()]; };
const median = xs => { const s = xs.filter(x => x != null).sort((a, b) => a - b), m = s.length >> 1; return !s.length ? null : s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const fmtH = h => h == null ? "" : h < 1 ? Math.round(h * 60) + " min" : h < 10 ? h.toFixed(1) + " h" : h < 48 ? Math.round(h) + " h" : Math.round(h / 24) + " d";
/* A batch's round is how far its typical proxy has got - the median of its
   proxies' uses - with its least- and most-used proxy beside it, so one
   proxy stuck on a phone cannot hold the whole batch back. In play, the
   proxies in play; set aside, the ones that were used. */
function roundState(exits) {
  const live = exits.filter(inPlay), used = exits.filter(e => (e.u || []).length);
  const pool = live.length ? live : used.length ? used : exits;
  const n = pool.map(e => (e.u || []).length).sort((a, b) => a - b);
  return {cur: Math.round(median(n) || 0), lo: n[0] || 0, hi: n[n.length - 1] || 0, of: pool.length, paused: !live.length};
}
function span(a, z) {
  if (a === z) return stamp(a);
  return a.slice(0, 10) === z.slice(0, 10) ? stamp(a) + "\u2013" + z.slice(11) : stamp(a) + " \u2013 " + stamp(z);
}

/* ----------------------------------------------------------------- render */
function seg(nm, label, opts, cur) {
  return '<span class="seg" role="radiogroup" aria-label="' + label + '">' + opts.map(([k, w, n]) =>
    '<button type="button" role="radio" aria-checked="' + (cur === k) + '" tabindex="' + (cur === k ? 0 : -1) + '" data-seg="' + nm + '" data-k="' + k + '">' + w + (n === undefined ? "" : ' <i class="tab">' + n + '</i>') + '</button>').join("") + '</span>';
}
function renderResp() {
  el("rangeseg").innerHTML = seg("range", "Range", RANGES, view.range);
  // Left: the phones finished in the range, by the key that ended each.
  const t = [0, 0, 0, 0, 0];
  EX.forEach(e => vOf(e).forEach((x, i) => { t[i] += x; }));
  const n = sum(t);
  let at = 0;
  const segs = K.map((k, i) => { const len = n ? t[i] / n * 100 : 0; const s = '<circle class="arc ' + k + '" cx="18" cy="18" r="15.9155" stroke-dasharray="' + len.toFixed(2) + " " + (100 - len).toFixed(2) + '" stroke-dashoffset="' + (-at).toFixed(2) + '"/>'; at += len; return s; }).join("");
  const when = {t: "today", d: "in the last 3 days", a: TRACK ? RANGE_WORD.a + ", when presses were first kept" : "so far"}[view.range];
  const legend = K.map((k, i) => '<div class="lr ' + k + '"><i class="' + k + '"></i><span>' + LABEL[i] + '</span><b class="tab">' + t[i] + '</b><small class="tab">' + (n ? pct(t[i], n) + "%" : "\u2014") + '</small></div>').join("");
  // Right: does a proxy wear out? Each view compares a proxy only with
  // itself - the same proxies in both columns - so a batch's quality, the
  // proxies set aside early and the day's weather cannot pass for wear.
  // By day is that weather itself. Each bucket is [phones, OR, Decline].
  // A phone counts in the range by when it was pressed - its use's end - as
  // the ring counts it. Which phone of the day it was goes by when it came.
  const pressAt = u => u[6] || u[0];
  const inRange = u => u[2] && pressAt(u) >= TRACK && pressAt(u) >= SINCE[view.range];
  const tally = () => [0, 0, 0];
  const add = (bk, u) => { bk[0]++; if (u[2] === "o") bk[1]++; if (u[2] === "x") bk[2]++; };
  const mode = view.axis;
  let buckets, names, units = 0, title, how;
  if (mode === "date") {
    const days = {};
    EX.forEach(e => (e.u || []).forEach(u => { if (inRange(u)) { const k = wday(pressAt(u)); add(days[k] || (days[k] = tally()), u); } }));
    const keys = Object.keys(days).sort().slice(-7);
    buckets = keys.map(k => days[k]);
    names = keys.map(wdayName);
    title = "How OR and Decline moved, day by day";
    how = "Every pressed phone of the pool by working day, noon to noon Tehran, the last 7 at most. Batches and hours change from day to day, so this is the farm\u2019s weather, not one proxy\u2019s wear.";
  } else {
    buckets = [tally(), tally()];
    EX.forEach(e => {
      const us = e.u || [];
      if (mode === "day") {
        // Days whose 1st phone was pressed and at least one later phone too:
        // the 1st against the later ones, the same proxy on the same day.
        const byDay = {};
        us.forEach(u => (byDay[wday(u[0])] = byDay[wday(u[0])] || []).push(u));
        Object.values(byDay).forEach(d => {
          const later = d.slice(1).filter(inRange);
          if (!inRange(d[0]) || !later.length) return;
          add(buckets[0], d[0]);
          later.forEach(u => add(buckets[1], u));
          units++;
        });
      } else if (mode === "round") {
        // Its pressed phones in order: the first half against the second.
        const ps = us.filter(inRange), h = ps.length >> 1;
        if (!h) return;
        ps.slice(0, h).forEach(u => add(buckets[0], u));
        ps.slice(ps.length - h).forEach(u => add(buckets[1], u));
        units++;
      } else {
        // Its pressed phones after under an hour of rest against after more.
        const rs = restsOf(e), ps = us.map((u, i) => [u, rs[i]]).filter(([u, r]) => r != null && inRange(u));
        const sh = ps.filter(([, r]) => r < 1), lo = ps.filter(([, r]) => r >= 1);
        if (!sh.length || !lo.length) return;
        sh.forEach(([u]) => add(buckets[0], u));
        lo.forEach(([u]) => add(buckets[1], u));
        units++;
      }
    });
    names = mode === "day" ? ["1st phone of its day", "Later phones that day"] : mode === "round" ? ["Its earlier uses", "Its later uses"] : ["After under 1 h of rest", "After 1 h or more"];
    title = mode === "day" ? "Is a proxy\u2019s later phone of the day worse?" : mode === "round" ? "Do a proxy\u2019s later uses go worse?" : "Does a short rest make a proxy worse?";
    how = mode === "day" ? "Only days on which a proxy\u2019s 1st phone and at least one later phone were both pressed, so both columns are the same proxies on the same days. A working day runs noon to noon, Tehran."
      : mode === "round" ? "Only proxies with 2 or more pressed phones: the first half of each one\u2019s phones on the left, the second half on the right, so both columns are the same proxies. Later uses are also later days; By day shows how the days moved."
      : "Rest runs from one phone leaving the proxy to the next one coming. Only proxies with pressed phones after both a short and a long rest, so both columns are the same proxies.";
  }
  const shares = buckets.map(([m, o, x]) => m >= 5 ? [pct(o, m), pct(x, m)] : null);
  const top = Math.max(20, Math.ceil(Math.max(0, ...shares.filter(Boolean).flat()) / 10) * 10);
  const cols = shares.map(s => !s ? '<div class="col2 few"><span class="nd">too few</span></div>'
    : '<div class="col2"><span class="wb o"><b class="tab">' + s[0] + '%</b><i style="height:' + (s[0] / top * 80).toFixed(1) + 'px"></i></span>' +
      '<span class="wb x"><b class="tab">' + s[1] + '%</b><i style="height:' + (s[1] / top * 80).toFixed(1) + 'px"></i></span></div>').join("");
  const labs = buckets.map(([m], i) => '<span>' + names[i] + '<small class="tab">' + m + ' phone' + (m === 1 ? "" : "s") + '</small></span>').join("");
  // Under it, the columns in words, and whether the OR gap is more than
  // chance alone gives on this many phones (a rough 95% line).
  let cap = buckets.length ? "Too few phones in this range to compare." : "No phone was pressed in this range.";
  if (mode === "date") {
    const ok = buckets.map((b, i) => [b, i]).filter(([b]) => b[0] >= 5);
    if (ok.length >= 2) {
      const o = ok.map(([b]) => pct(b[1], b[0])), x = ok.map(([b]) => pct(b[2], b[0]));
      cap = "Across " + ok.length + " working days, OR moved between " + Math.min(...o) + "% and " + Math.max(...o) + "%, and Decline between " + Math.min(...x) + "% and " + Math.max(...x) + "%.";
    }
  } else if (shares[0] && shares[1]) {
    const [b0, b1] = buckets, p = (b0[1] + b1[1]) / (b0[0] + b1[0]);
    const gap = Math.abs(b0[1] / b0[0] - b1[1] / b1[0]), line = 2 * Math.sqrt(p * (1 - p) * (1 / b0[0] + 1 / b1[0]));
    const who = mode === "day" ? units + " proxy-day" + (units === 1 ? "" : "s") : units + (units === 1 ? " proxy" : " proxies");
    cap = names[0] + ": OR " + shares[0][0] + "%, Decline " + shares[0][1] + "%. " + names[1] + ": OR " + shares[1][0] + "%, Decline " + shares[1][1] + "%. On " + who + ", " +
      (gap <= line ? "an OR gap this size is within what chance alone gives." : "the OR gap is larger than chance alone gives.");
  }
  // Each half is drawn again only when it changed, and then it is fresh:
  // its ring draws or its bars grow. A redraw that changed nothing is still.
  const L = '<div class="zh"><span class="tt"><b>Phones finished</b><small>' + n + ' phone' + (n === 1 ? '' : 's') + ' ' + when + ', by the key the operator pressed to finish each.</small></span></div>' +
    '<div class="z1b"><span class="donut"><svg class="ring" viewBox="0 0 36 36" aria-hidden="true"><circle class="bg" cx="18" cy="18" r="15.9155"/>' + segs + '</svg><b class="tab"><span class="num">' + n + '</span><small>phones</small></b></span><div class="legend">' + legend + '</div></div>';
  const R = '<div class="zh"><span class="tt"><b>' + title + '</b><small>' + how + '</small></span>' + seg("axis", "Compare", [["day", "Within a day"], ["round", "Over its life"], ["rest", "By rest"], ["date", "By day"]], view.axis) + '</div>' +
    '<div class="wear"><div class="cols" style="--n:' + Math.max(1, buckets.length) + '">' + cols + '</div><div class="cl" style="--n:' + Math.max(1, buckets.length) + '">' + labs + '</div></div>' +
    '<div class="wl"><span><i class="o"></i>OR</span><span><i class="x"></i>Decline</span><span>each bar: the share of that column\u2019s phones</span></div>' +
    '<p class="wcap">' + cap + '</p>';
  if (L === respWas.l && R === respWas.r) return;
  el("resp").innerHTML = '<div class="z1' + (L !== respWas.l ? ' fresh' : '') + '">' + L + '</div><div class="z2' + (R !== respWas.r ? ' fresh' : '') + '">' + R + '</div>';
  respWas.l = L; respWas.r = R;
}
const respWas = {l: "", r: ""};
let famSeq = null, famsDrawn = false;
function famList() {
  const m = new Map();
  for (const e of EX) {
    const key = famKey(e);
    let f = m.get(key);
    if (!f) m.set(key, f = {key, names: new Map(), type: e.type || "", dates: new Set(), ex: [], today: 0, free: 0, phone: 0, aside: 0, dead: 0, lanes: new Set(), v: [0, 0, 0, 0, 0]});
    const seller = e.seller || e.f;
    f.names.set(seller, (f.names.get(seller) || 0) + 1);
    if (e.added) f.dates.add(e.added);
    f.ex.push(e);
    f.today += e.today || 0;
    f[e.s === "free" ? "free" : e.s === "phone" ? "phone" : e.s === "dead" ? "dead" : "aside"]++;
    if (e.s === "phone" && e.after) f.leaving = (f.leaving || 0) + 1;
    f.lanes.add(e.lane);
    vOf(e).forEach((x, i) => { f.v[i] += x; });
  }
  const order = {keep: 0, watch: 1, stop: 2, few: 3};
  return [...m.values()].map(f => {
    f.name = [...f.names.entries()].sort((a, b) => b[1] - a[1])[0][0];
    // The day it came; an older group added over several days shows the span.
    const ds = [...f.dates].sort(), a = stamp(ds[0]), z = stamp(ds[ds.length - 1]);
    f.when = !ds.length ? "" : a === z ? a : a + " \u2013 " + z;
    f.label = [f.name, f.type].filter(Boolean).join(" ") + (f.when ? " \u00b7 " + f.when : "");
    f.leaving = f.leaving || 0;
    // In play while any proxy is handed out - free, or on a phone it keeps.
    f.on = f.ex.some(e => e.s === "free" || (e.s === "phone" && !e.after));
    f.n = sum(f.v); f.adv = advice(f.v); return f;
  })
    .sort((a, b) => b.on - a.on || order[a.adv[0]] - order[b.adv[0]] || (pct(b.v[0], b.n) ?? -1) - (pct(a.v[0], a.n) ?? -1) || b.n - a.n)
    // The order is taken once, when the page opens, and then kept: a card does
    // not jump away from the switch just pressed. A batch added since goes first.
    .sort((a, b) => (famSeq ? (famSeq.has(a.key) ? famSeq.get(a.key) : -1) - (famSeq.has(b.key) ? famSeq.get(b.key) : -1) : 0))
    .map((f, i, all) => { if (!famSeq) famSeq = new Map(all.map((x, n) => [x.key, n])); return f; });
}
/* The most common values first, as [value, count]. */
const tally = xs => [...xs.reduce((m, x) => m.set(x, (m.get(x) || 0) + 1), new Map())].sort((a, b) => b[1] - a[1]);
const DOT = {phone: "p", free: "f", aside: "", dead: "x"}, DOT_ORDER = {phone: 0, free: 1, aside: 2, dead: 3};
function renderFams() {
  const all = famList(), live = all.filter(f => f.on).length;
  el("famsum").innerHTML = '<b class="tab">' + live + '</b> in play \u00b7 <b class="tab">' + (all.length - live) + '</b> set aside';
  const still = famsDrawn ? " still" : "";
  famsDrawn = true;
  el("fams").innerHTML = all.map((f, i) => {
    const one = f.lanes.size === 1 ? [...f.lanes][0] : "";
    // A dot a proxy: on a phone, free, set aside, dead - in that order.
    const dots = f.ex.slice().sort((a, b) => DOT_ORDER[a.s] - DOT_ORDER[b.s]).map(e => '<i class="' + DOT[e.s] + (e.s === "phone" && e.after ? " l" : "") + '"></i>').join("");
    const kept = f.phone - f.leaving;
    const parts = [kept && '<span class="p">' + kept + ' on phones</span>', f.leaving && '<span class="p">' + f.leaving + ' on phones, then aside</span>', f.free && '<span class="f">' + f.free + ' free</span>',
      f.aside && f.aside + ' set aside', f.dead && '<span class="x">' + f.dead + ' dead</span>'].filter(Boolean).join(" \u00b7 ");
    const d = pct(f.v[0], f.n), o = pct(f.v[2], f.n);
    // Where the batch comes out: its countries (a count only when they
    // differ, amber outside the US), its commonest ISP, and its daily cap.
    const ccs = tally(f.ex.map(e => e.cc || "?"));
    const where = ccs.slice(0, 2).map(([c, k]) => '<span class="cc' + (c !== "US" && c !== "?" ? " off" : "") + '">' + esc(c) + '</span>' + (ccs.length > 1 ? '<span class="tab">' + k + '</span>' : '')).join("");
    const isps = tally(f.ex.map(e => e.isp).filter(Boolean));
    const isp = isps.length ? esc(isps[0][0]) + (isps.length > 1 ? " +" + (isps.length - 1) : "") : "not read yet";
    const caps = [...new Set(f.ex.map(e => e.cap || 0))];
    const capw = caps.length > 1 ? "caps vary" : caps[0] ? caps[0] + " a day" : "no cap";
    const rs = roundState(f.ex), x = pct(f.v[1], f.n);
    const off = !f.on;
    // Where its proxies stand: its typical proxy's round on a scale every card
    // shares, its least- and most-used proxy either side; then its typical rest.
    const rest = median(f.ex.flatMap(restsOf)), most = Math.max(1, ...EX.map(e => (e.u || []).length));
    const at = v => (v / most * 100).toFixed(1) + "%";
    const rnd = '<span class="rnd' + (off ? " paused" : "") + '"><b>' + (rs.hi ? "Round " + rs.cur : "Not used yet") + '</b>' +
      '<span class="rbar">' + (rs.hi ? '<i style="left:' + at(rs.lo) + ';width:' + at(rs.hi - rs.lo) + '"></i><u style="left:' + at(rs.cur) + '"></u>' : '') + '</span><small>' +
      [off ? "set aside" : "", rs.hi ? (rs.lo === rs.hi ? "all at " + rs.hi + (rs.hi === 1 ? " use" : " uses") : "uses " + rs.lo + "\u2013" + rs.hi) : "", rest != null ? "rest " + fmtH(rest) : ""].filter(Boolean).join(" \u00b7 ") + '</small></span>';
    const k = esc(f.key), mk = "b|" + f.key + "|more";
    return '<div class="fam ' + (one || "either") + (view.fam === f.key ? " on" : "") + (off ? " paused" : "") + (flashFam === f.key ? " flash" : "") + still + '" style="--i:' + i + '">' +
      '<button class="hit" type="button" data-fam="' + k + '" aria-pressed="' + (view.fam === f.key) + '" aria-label="Show only the proxies of ' + esc(f.label) + '"></button>' +
      '<span class="top">' + tile(one) + '<span class="nm"><b>' + esc(f.name) + '</b><small>' + (f.type ? '<span class="ty">' + esc(f.type) + '</span> \u00b7 ' : '') + esc(f.when) + '</small></span><span class="adv ' + f.adv[0] + '">' + f.adv[1] + '</span>' +
      '<button class="sw' + (litFam === f.key ? ' lit' : '') + '" type="button" role="switch" aria-checked="' + f.on + '" data-bsw="' + k + '" aria-label="' + esc(f.label) + ' in play" title="' + (f.on ? "In play: turn off to set the whole batch aside" : "Set aside: turn on to put the whole batch back in play") + '"><i></i></button>' +
      '<button class="bm' + (menu === mk ? " on" : "") + '" type="button" data-menu="' + esc(mk) + '" aria-haspopup="dialog" aria-expanded="' + (menu === mk) + '" aria-label="Batch actions for ' + esc(f.label) + '">' + SVG(ICON.more, 2) + '</button></span>' +
      '<span class="dots"><span class="dd" aria-hidden="true">' + dots + '</span><small>' + parts + '</small></span>' + rnd +
      '<span class="rates"><span class="rate ' + (f.n ? "g" : "na") + '"><b class="tab">' + (f.n ? d + "%" : "\u2014") + '</b><small>Done</small></span>' +
      '<span class="rate ' + (f.n ? "x" : "na") + '"><b class="tab">' + (f.n ? x + "%" : "\u2014") + '</b><small>Decl</small></span>' +
      '<span class="rate ' + (f.n ? "o" : "na") + '"><b class="tab">' + (f.n ? o + "%" : "\u2014") + '</b><small>OR</small></span>' +
      '<span class="rate"><b class="tab">' + f.n + '</b><small>phones</small></span>' +
      '<span class="rate' + (f.today ? "" : " na") + '"><b class="tab">' + f.today + '</b><small>today</small></span></span>' +
      (f.n ? mixBar(f.v) : '<span class="mix"></span>') +
      '<span class="where">' + where + '<span class="isp">' + isp + '</span><span class="capw">' + capw + '</span></span></div>';
  }).join("");
  flashFam = "";
}
function renderTools() {
  const cnt = (skip, f) => EX.filter(e => passes(e, skip) && f(e)).length, all = () => true;
  el("scopeseg").innerHTML = seg("scope", "Show", [["all", "All", cnt("scope", all)], ["play", "In play", cnt("scope", inPlay)], ["aside", "Set aside", cnt("scope", e => !inPlay(e))]], view.scope);
  el("laneseg").innerHTML = seg("lane", "Lane", [["", "Any lane", cnt("lane", all)], ["gpt", "GPT", cnt("lane", e => e.lane !== "spotify")], ["spotify", "Spotify", cnt("lane", e => e.lane !== "gpt")]], view.lane);
  el("sortbox").innerHTML = '<button class="sortb' + (menu === "sort" ? " on" : "") + '" type="button" data-menu="sort" aria-haspopup="menu" aria-expanded="' + (menu === "sort") + '" aria-label="Sort: ' + sortWord(view.sort, view.dir) + '"><small>Sort</small><b>' + sortWord(view.sort, view.dir) + '</b>' + SVG(ICON.chev, 2.2) + '</button>';
  const fam = view.fam ? (famList().find(f => f.key === view.fam) || {}).label : "";
  el("famchip").innerHTML = fam ? '<button class="chip" type="button" data-clear="fam" aria-label="Show every batch, not only ' + esc(fam) + '"><span>Batch ' + esc(fam) + '</span>' + SVG(ICON.cross, 2.6) + '</button>' : "";
  // Show only: a rule with nothing in it is drawn faint; Dead only when there is one.
  const fl = FLAGS.map(f => [f, cnt("flag", f.test)]).filter(([f, n]) => n || !f.rare || view.flag === f.k), on = view.flag ? FLAG[view.flag] : null;
  el("flags").innerHTML = '<span class="k">Show only</span>' + fl.map(([f, n]) =>
    '<button type="button" class="flag ' + f.c + '" data-flag="' + f.k + '" aria-pressed="' + (view.flag === f.k) + '"' + (n || view.flag === f.k ? '' : ' disabled') + '>' + f.w + ' <b class="tab">' + n + '</b></button>').join("") +
    (narrowed() ? '<button class="link clr" type="button" data-clear="all">Clear filters</button>' : '');
  el("flagwhy").innerHTML = on ? '<p class="flagwhy"><b>' + on.w + '</b> \u00b7 ' + on.why() + '</p>' : '';
}
function passes(e, skip) {
  if (skip !== "scope" && view.scope !== "all" && (view.scope === "play") !== inPlay(e)) return false;
  if (skip !== "lane" && view.lane && e.lane !== view.lane && e.lane !== "") return false;
  if (skip !== "flag" && view.flag && !FLAG[view.flag].test(e)) return false;
  if (view.fam && famKey(e) !== view.fam) return false;
  if (view.q && ![e.n, nameText(e), e.ip, e.serial, e.isp, e.cc].join(" ").toLowerCase().includes(view.q.toLowerCase())) return false;
  return true;
}
const narrowed = () => view.scope !== "all" || !!view.lane || !!view.flag || !!view.fam || !!view.q;
function shown() {
  const rows = EX.filter(e => passes(e));
  const nm = (a, b) => a.n.localeCompare(b.n, undefined, {numeric: true});
  const S = SORTS[view.sort] || SORTS.name, more = (a, b) => sum(vOf(b)) - sum(vOf(a));
  rows.sort((a, b) => {
    if (!S.val) return nm(a, b) * view.dir;
    const x = S.val(a), y = S.val(b);
    if (x == null || y == null) return x == null && y == null ? nm(a, b) : x == null ? 1 : -1;
    const d = typeof x === "string" ? x.localeCompare(y) : x - y;
    return d * view.dir || more(a, b) || nm(a, b);
  });
  return rows;
}
function todayCell(e) {
  const n = e.today, c = e.cap, cls = c && n >= c ? "capped" : n >= 4 ? "hot" : n === 3 ? "warm" : "";
  return '<span class="today ' + cls + '">' + n + (c ? '<small>/ ' + c + '</small>' : '') + '</span>';
}
function ipCell(e) {
  if (!e.ip) return '<span class="ipc none"><b>Not read yet</b><small>read on its next test</small></span>';
  return '<span class="ipc"><b>' + esc(e.ip) + '<span class="cc' + (isOff(e) ? " off" : "") + '">' + esc(e.cc || "?") + '</span></b><small>' + esc(e.isp || "") + '</small></span>';
}
/* `top` is the most phones any proxy ended in this range: the bar's full length. */
function respCell(e, top) {
  const v = vOf(e), n = sum(v);
  const rate = (i, c) => '<b class="tab ' + c + (n ? "" : " none") + '">' + (n ? pct(v[i], n) + "%" : "\u2014") + '</b>';
  const fill = n ? '<span class="fill" style="width:' + Math.max(3, n / top * 100).toFixed(1) + '%">' + K.map((k, i) => v[i] ? '<i class="' + k + '" style="flex:' + v[i] + '"></i>' : "").join("") + '</span>' : "";
  return '<span class="r5"><span class="nums"><b class="tab n' + (n ? "" : " zero") + '">' + n + '</b>' +
    K.map((k, i) => '<b class="tab ' + k + (v[i] ? " live" : "") + '">' + v[i] + '</b>').join("") + rate(0, "g") + rate(1, "xr") + rate(2, "r") + '</span>' +
    '<span class="vol" aria-hidden="true">' + fill + '</span></span>';
}
function doors(e) {
  const id = e.id, open = k => menu === id + ":" + k, who = esc(nameText(e));
  const door = (k, cls, pop, label, body) => '<button type="button" class="' + cls + (open(k) ? " on" : "") + '" data-menu="' + id + ':' + k + '" aria-haspopup="' + pop + '" aria-expanded="' + open(k) + '" aria-label="' + label + '">' + body + '</button>';
  return '<span class="doors">' +
    door("lane", "", "menu", "Lane of " + who + ": " + laneWord(e.lane), SVG(ICON.lane, 1.9) + laneWord(e.lane) + SVG(ICON.chev, 2.2)) +
    door("cap", "", "dialog", "Daily cap of " + who + ": " + (e.cap ? e.cap + " a day" : "none"), SVG(ICON.cap, 1.9) + (e.cap ? e.cap + "/day" : "No cap") + SVG(ICON.chev, 2.2)) +
    door("more", "more", "menu", "More for " + who, SVG(ICON.more, 2)) + '</span>';
}
function renderRows() {
  const rows = shown(), top = Math.max(1, ...EX.map(x => sum(vOf(x))));
  el("rows").innerHTML = rows.length ? rows.map(e =>
    '<tr data-id="' + e.id + '" class="' + (ticked.has(e.id) ? "ticked " : "") + (isOff(e) ? "off " : "") + (flashIds.has(e.id) ? "flash" : "") + '">' +
    '<td class="tk"><button class="box' + (ticked.has(e.id) ? " on" : "") + '" type="button" role="checkbox" aria-checked="' + ticked.has(e.id) + '" data-tick="' + e.id + '" aria-label="Select ' + esc(nameText(e)) + '">' + SVG(ICON.tick, 3.4) + '</button></td>' +
    '<td><span class="nm2" data-open="' + e.id + '" role="button" tabindex="0" title="Open its story">' + nameHtml(e) + '</span></td>' +
    '<td><span class="stc">' + psw(e) + '<span>' + pill(e) + '</span></span></td><td><span class="lane">' + tile(e.lane, true) + '<b>' + laneWord(e.lane) + '</b></span></td>' +
    '<td>' + ipCell(e) + '</td><td>' + todayCell(e) + '</td><td>' + respCell(e, top) + '</td>' +
    '<td>' + doors(e) + '</td></tr>').join("")
    : '<tr class="none-row"><td colspan="8"><b>No proxy matches that.</b>' + (narrowed() ? '<button class="link" type="button" data-clear="all">Clear filters</button>' : '') + '</td></tr>';
  flashIds.clear();
  el("count").innerHTML = '<b class="tab">' + rows.length + '</b> of <b class="tab">' + EX.length + '</b>';
  el("shown").innerHTML = '<b class="tab">' + rows.length + '</b> of <b class="tab">' + EX.length + '</b> proxies shown';
  el("archn").textContent = archived;
  const every = rows.length > 0 && rows.every(e => ticked.has(e.id)), some = rows.some(e => ticked.has(e.id));
  el("all").classList.toggle("on", every);
  el("all").setAttribute("aria-checked", every ? "true" : some ? "mixed" : "false");
  document.querySelectorAll("thead th.sortable").forEach(th => {
    const on = th.dataset.sort === view.sort;
    th.classList.toggle("up", on && view.dir === 1);
    th.classList.toggle("down", on && view.dir === -1);
    th.setAttribute("aria-sort", on ? (view.dir === 1 ? "ascending" : "descending") : "none");
  });
}
function renderPop() {
  const p = el("pop"), d = el("layer").querySelector(".draw"), dp = d && d.querySelector(".dpop");
  if (!menu) { p.innerHTML = ""; if (dp) dp.remove(); return; }
  // The drawer's door before the same door in the list behind it.
  const at = '[data-menu="' + menu + '"]', btn = el("layer").querySelector(at) || document.querySelector(at);
  if (!btn) { menu = ""; p.innerHTML = ""; return; }
  const inDraw = !!btn.closest(".draw");
  const [idS, kind] = menu.split(":");
  const id = +idS, e = EX.find(x => x.id === id);
  let html = "";
  if (menu === "sort") html = SORT_MENU.map(g => g.map(k => '<button type="button" role="menuitemradio" aria-checked="' + (view.sort === k) + '" class="' + (view.sort === k ? "on" : "") + '" data-sortk="' + k + '">' + sortWord(k, SORTS[k].dir) + '</button>').join("")).join('<span class="sepm"></span>');
  else if (menu.startsWith("b|")) html = batchMenu(menu.slice(2, -5));
  else if (kind === "lane") html = [["gpt", "Keep for GPT"], ["spotify", "Keep for Spotify"], ["", "Either lane"]].map(([l, w]) => '<button type="button" class="' + (e && e.lane === l ? "on" : "") + '" data-do="lane:' + l + '" data-id="' + id + '">' + (l ? tile(l, true) : SVG(ICON.lane, 1.9)) + w + '</button>').join("");
  else if (kind === "cap") html = '<div class="capm"><span class="k">Phones a day</span><span class="step"><button type="button" data-step="-1" aria-label="One phone fewer">\u2212</button><input id="pop-cap" value="' + (e ? e.cap : 0) + '" inputmode="numeric" autocomplete="off" aria-label="Phones a day, 0 for no cap"><button type="button" data-step="1" aria-label="One phone more">+</button><small>' + (e && e.cap ? "a day" : "no cap") + '</small></span>' +
    '<span class="row"><button type="button" class="on" data-do="capset" data-id="' + id + '">Set</button><button type="button" data-do="cap:0" data-id="' + id + '">No cap</button></span></div>';
  else html = (inDraw ? '' : '<button type="button" data-do="story" data-id="' + id + '">' + SVG(ICON.phone, 2) + 'Open its story</button>') + '<button type="button" data-do="test" data-id="' + id + '">' + SVG(ICON.reload, 2) + 'Test now</button><button type="button" class="bad" data-do="remove" data-id="' + id + '">' + SVG(ICON.trash, 2) + 'Remove</button>';
  // A list of choices is a menu; one with a stepper in it is a small dialog.
  const isMenu = menu === "sort" || kind === "lane" || kind === "more";
  const label = menu === "sort" ? "Sort" : menu.startsWith("b|") ? "Batch actions" : kind === "lane" ? "Lane" : kind === "cap" ? "Phones a day" : "More";
  // Opened in the drawer, the menu lives inside it: the drawer stays the
  // one modal a screen reader is in, and its menu is part of it.
  let host = p;
  if (inDraw) { p.innerHTML = ""; host = dp || d.appendChild(Object.assign(document.createElement("div"), {className: "dpop"})); }
  else if (dp) dp.remove();
  host.innerHTML = '<div class="pop" role="' + (isMenu ? "menu" : "dialog") + '" aria-label="' + label + '">' + html + '</div>';
  if (isMenu) host.querySelectorAll(".pop>button").forEach(b => { if (!b.getAttribute("role")) b.setAttribute("role", "menuitem"); });
  el("said").classList.remove("up"); // the last note is read; it must not cover the menu
  const r = btn.getBoundingClientRect(), m = host.firstElementChild, mw = m.offsetWidth, mh = m.offsetHeight;
  let left = Math.min(r.left, window.innerWidth - mw - 8), top = r.bottom + 6;
  if (top + mh > window.innerHeight - 8) top = r.top - mh - 6;
  m.style.left = Math.max(8, left) + "px"; m.style.top = Math.max(8, top) + "px";
}
/* A batch's menu: everything the proxies' doors do, for all of them at once. */
function batchMenu(key) {
  const f = famList().find(x => x.key === key);
  if (!f) return "";
  const k = esc(key), lanes = [...f.lanes], caps = [...new Set(f.ex.map(e => e.cap || 0))];
  const cap = caps.length === 1 ? caps[0] : 2;
  return '<span class="mh"><b>' + esc(f.label) + '</b><small>' + f.ex.length + (f.ex.length === 1 ? ' proxy' : ' proxies') + ', all at once</small></span>' +
    [["gpt", "Keep all for GPT"], ["spotify", "Keep all for Spotify"], ["", "All on either lane"]].map(([l, w]) =>
      '<button type="button" class="' + (lanes.length === 1 && lanes[0] === l ? "on" : "") + '" data-bdo="lane:' + l + '" data-key="' + k + '">' + (l ? tile(l, true) : SVG(ICON.lane, 1.9)) + w + '</button>').join("") +
    '<span class="sepm"></span><div class="capm"><span class="k">Phones a day, each proxy</span><span class="step"><button type="button" data-step="-1" aria-label="One phone fewer">\u2212</button><input id="pop-cap" value="' + cap + '" inputmode="numeric" autocomplete="off" aria-label="Phones a day, 0 for no cap"><button type="button" data-step="1" aria-label="One phone more">+</button><small>' + (cap ? "a day" : "no cap") + '</small></span>' +
    '<span class="row"><button type="button" class="on" data-bdo="capset" data-key="' + k + '">Set</button><button type="button" data-bdo="cap:0" data-key="' + k + '">No cap</button></span></div><span class="sepm"></span>' +
    '<button type="button" data-bdo="test" data-key="' + k + '">' + SVG(ICON.reload, 2) + 'Test every proxy</button>' +
    '<button type="button" data-bdo="tick" data-key="' + k + '">' + SVG(ICON.tick, 2.4) + 'Tick its proxies in the list</button>';
}
/* The switch: the whole batch in play, or set aside. Free proxies leave the
   shelf now and set-aside or dead ones are tested back onto it; one on a
   phone stays with its phone and follows. */
function setBatch(key, on) {
  const f = famList().find(x => x.key === key);
  if (!f) return;
  apply(on ? "free" : "aside", new Set(f.ex.map(e => e.id)), f.label, false, key);
}
function batchDo(kind, key) {
  const f = famList().find(x => x.key === key);
  if (!f) return;
  const ids = new Set(f.ex.map(e => e.id));
  if (kind === "tick") {
    ids.forEach(id => ticked.add(id)); menu = ""; Object.assign(view, {fam: key, scope: "all"});
    renderAll(); el("s-ex").scrollIntoView({behavior: calm ? "auto" : "smooth", block: "start"});
    say("The " + ids.size + (ids.size === 1 ? " proxy" : " proxies") + " of " + f.label + (ids.size === 1 ? " is" : " are") + " ticked; the bar below acts on them.");
    return;
  }
  menu = "";
  apply(kind === "capset" ? "cap:" + capFrom(el("pop-cap")) : kind, ids, f.label, false, key);
}
function renderSel() {
  const host = el("sel");
  if (!ticked.size) { host.innerHTML = ""; document.querySelector("main").style.paddingBottom = ""; return; }
  const sel = EX.filter(e => ticked.has(e.id)), on = sel.filter(inPlay).length, hid = sel.filter(e => !passes(e)).length;
  const st = on === sel.length ? "true" : on ? "mixed" : "false";
  // From all on a press sets them aside; from some or none it puts them all in play.
  // Ticked rows a filter hides are still acted on - so the bar says so.
  const inner = '<b>' + ticked.size + ' selected' + (hid ? '<small>' + hid + ' not shown</small>' : '') + '</b>' +
    '<span class="sws"><button class="sw" type="button" role="checkbox" aria-checked="' + st + '" data-bulk="' + (st === "true" ? "aside" : "free") + '" data-keep="1" aria-label="The ticked proxies in play"><i></i></button>' +
    '<span>In play<small class="tab">' + on + ' of ' + sel.length + '</small></span></span>' +
    '<button class="act" type="button" data-bulk="lane:gpt">Keep for GPT</button><button class="act" type="button" data-bulk="lane:spotify">Keep for Spotify</button>' +
    '<button class="act" type="button" data-bulk="cap:2">Cap 2/day</button><button class="act" type="button" data-bulk="cap:0">No cap</button>' +
    '<button class="act" type="button" data-bulk="test">Test</button><button class="act bad" type="button" data-bulk="remove">Remove</button>' +
    '<button class="xs" type="button" data-bulk="clear" aria-label="Clear the selection">' + SVG(ICON.cross, 2.2) + '</button>';
  const bar = host.querySelector(".sel");
  if (bar) bar.innerHTML = inner; else host.innerHTML = '<div class="sel">' + inner + '</div>';
  // The page leaves room under the bar, however many lines it wraps to.
  document.querySelector("main").style.paddingBottom = host.firstElementChild.offsetHeight + 60 + "px";
}
/* The chosen batch's rounds, above its proxies. Lifetime, whatever the range:
   a round is a count of uses, not a stretch of time. */
function renderRounds() {
  const box = el("rounds"), f = view.fam ? famList().find(x => x.key === view.fam) : null;
  if (!f) { box.innerHTML = ""; return; }
  const R = roundsOf(f.ex), rs = roundState(f.ex), dash = '<span class="si none">\u2014</span>';
  const rows = R.map(r => {
    const n = sum(r.v), now = r.k === rs.cur && !rs.paused;
    const rr = (i, c) => '<span class="rr ' + c + (n ? (n < 8 ? " pale" : "") : " none") + ' tab">' + (n ? pct(r.v[i], n) + "%" : "\u2014") + '</span>';
    return '<tr' + (now ? ' class="now"' : '') + '><td><span class="rk">R' + r.k + '</span>' + (now ? '<span class="nowp">now</span>' : '') + (r.ips ? '<span class="nip">new IP \u00d7' + r.ips + '</span>' : '') + '</td>' +
      '<td><span class="ex tab">' + r.uses + '<small> of ' + f.ex.length + '</small></span></td>' +
      '<td>' + (r.k === 1 ? '<span class="si none">first use</span>' : r.rests.length ? '<span class="ex tab">' + fmtH(median(r.rests)) + '</span>' : dash) + '</td>' +
      '<td>' + (r.holds.length ? '<span class="ex tab">' + fmtH(median(r.holds)) + '</span>' : dash) + '</td>' +
      '<td>' + (r.builds ? '<span class="si tab">' + pct(r.signed, r.builds) + '%<small> of ' + r.builds + '</small></span>' : '<span class="si none">moved in</span>') + '</td>' +
      '<td>' + (r.tracked ? '<span class="p5"><span class="g5">' + K.map((k, i) => '<b class="tab ' + k + (r.v[i] ? " live" : "") + '">' + r.v[i] + '</b>').join("") + '</span>' + (n ? mixBar(r.v) : '<span class="mix"></span>') + '</span>'
        : '<span class="untr">before presses were kept</span>') + '</td>' +
      '<td>' + rr(0, "g") + '</td><td>' + rr(1, "x") + '</td><td>' + rr(2, "o") + '</td><td><span class="when">' + esc(span(r.first, r.last)) + '</span></td></tr>';
  }).join("");
  const where = !rs.hi ? "not used yet" : (rs.paused ? "set aside at round " + rs.cur : "its typical proxy is at round " + rs.cur) + " \u00b7 " + (rs.lo === rs.hi ? "all at " + rs.hi : "least " + rs.lo + ", most " + rs.hi);
  box.innerHTML = '<div class="card rp"><div class="rph"><b>Rounds</b><small>' + esc(f.label) + ' \u00b7 ' + where + '</small><span class="sp"></span>' +
    '<small>Round k is each proxy\u2019s k-th phone. Rest before: how long its proxies sat idle, from the previous phone leaving to this one coming; held: how long this round\u2019s phones kept them. Done, Decline and OR are shares of the phones pressed; pale under 8.</small></div>' +
    '<div class="wrap rscroll"><table class="rt"><thead><tr><th>Round</th><th>Proxies</th><th>Rest before</th><th>Held</th><th>Signed in</th>' +
    '<th>Presses<span class="g5"><span class="d">Done</span><span class="x">Decl</span><span class="o">OR</span><span class="a">Auth</span><span class="f">Fail</span></span></th>' +
    '<th>Done %</th><th>Decl %</th><th>OR %</th><th>When</th></tr></thead><tbody>' +
    (rows || '<tr class="none-row"><td colspan="10">No proxy of this batch has been used yet.</td></tr>') + '</tbody></table></div></div>';
}
function renderAll() { renderResp(); renderFams(); renderTools(); renderRounds(); renderRows(); renderPop(); renderSel(); }

/* ----------------------------------------------------------------- actions */
function say(text, ok) {
  const n = el("said");
  // A new note starts its clock again.
  n.classList.remove("up"); void n.offsetWidth;
  n.textContent = text; n.classList.toggle("ok", !!ok);
  clearTimeout(noteTimer); noteTimer = setTimeout(() => n.classList.remove("up"), 3600);
  // Placed once the press has finished drawing - a Remove may have closed
  // the drawer meanwhile: over an open drawer above its doors, in its
  // middle; over the selection bar, above the bar - never on either.
  queueMicrotask(() => {
  const f = document.querySelector(".draw footer"), bar = document.querySelector(".sel");
  if (f) { const r = f.getBoundingClientRect(); Object.assign(n.style, {bottom: (innerHeight - r.top + 12) + "px", left: (r.left + r.width / 2) + "px", maxWidth: (r.width - 24) + "px"}); }
  else if (bar) { const r = bar.getBoundingClientRect(); Object.assign(n.style, {bottom: (innerHeight - r.top + 10) + "px", left: "", maxWidth: ""}); }
  else Object.assign(n.style, {bottom: "", left: "", maxWidth: ""});
  n.classList.add("up");
  const inner = document.querySelector(".draw [role=status]");
  if (inner) inner.textContent = text;
  });
}
/* A press, sent. The farm answers with each proxy's outcome and the pool as
   it now stands: what it did at once is drawn at once, and what it must test
   first is marked and watched to its end (watch). */
function apply(kind, ids, who, endSelection, fam) {
  const rows = EX.filter(e => ids.has(e.id));
  if (menu) closeMenu(); else renderPop();
  if (endSelection) ids.forEach(id => ticked.delete(id));
  if (!rows.length) { renderAll(); return; }
  busy++;
  send("/pools/proxy/do", {what: kind, ids: rows.map(e => e.id).join(",")}).then(a => {
    busy--;
    if (!a.ok) { renderAll(); redrawDrawer(); say(a.note || "Nothing was changed."); return; }
    const items = a.items || [], by = new Map(items.map(i => [i.id, i]));
    // Newer than any state a poll already on its way will bring.
    held = null; drawnAt = ++seq;
    const went = items.filter(i => i.said === "done"), waiting = items.filter(i => i.said === "queued" || i.said === "pending");
    if (a.state) load(a.state);
    if (kind === "remove") {
      went.forEach(i => ticked.delete(i.id));
      // A batch whose last proxy went takes its filter with it.
      if (view.fam && !EX.some(e => famKey(e) === view.fam)) view.fam = "";
    }
    went.concat(waiting).forEach(i => flashIds.add(i.id));
    if (kind === "free") went.forEach(i => { const e = EX.find(x => x.id === i.id); if (e && inPlay(e)) litIds.add(i.id); });
    if (fam && (went.length || waiting.length)) { flashFam = fam; if (kind === "free") litFam = fam; }
    if (waiting.length) watch(kind, waiting, who, rows);
    renderAll(); redrawDrawer();
    setTimeout(() => litIds.clear());
    litFam = "";
    const [text, ok] = told(kind, rows, by, who);
    say(text, ok);
  });
}
const cap1 = s => s ? s[0].toUpperCase() + s.slice(1) : s;
const stop = s => /[.!?]$/.test(s) ? s : s + ".";
/* The farm's sentence about a proxy, in the page's words: its name as the
   list shows it, and where it comes out rather than "exit". */
const inWords = (e, note) => (e ? String(note || "").split(e.n).join(nameText(e)) : String(note || "")).replace(/\(exit ([^)]*)\)/, "(comes out at $1)");
/* The sentence for a press: how many it moved and how, and the first reason
   given for any it could not move. */
function told(kind, rows, by, who) {
  const its = rows.map(e => [e, by.get(e.id) || {said: "gone"}]);
  const went = its.filter(([, i]) => i.said === "done"), wait = its.filter(([, i]) => i.said === "queued" || i.said === "pending");
  const no = its.filter(([, i]) => !["done", "queued", "pending", "skip"].includes(i.said));
  const why = !no.length ? "" : no[0][1].said === "gone" ? nameText(no[0][0]) + " is no longer in the pool"
    : cap1(inWords(no[0][0], no[0][1].note) || nameText(no[0][0]) + " could not be changed");
  const rest = no.length > 1 ? " And " + (no.length - 1) + " more could not." : "";
  const n = went.length + wait.length;
  if (!n) return no.length ? [stop(why) + rest, false] : ["Nothing to change: already that way.", false];
  const pl = (one, many) => n === 1 ? one : many;
  let words;
  if (kind === "free") {
    words = !wait.length ? "kept in play after " + pl("its phone", "their phones")
      : !went.length ? pl("being tested, and back in play if it answers", "being tested, and back in play as each answers")
      : "back in play: " + went.length + " kept on their phones, " + wait.length + " being tested first";
  } else if (kind === "aside") {
    const waits = went.filter(([e]) => e.s === "phone").length;
    words = waits && waits === n ? "set aside once " + pl("its phone goes", "their phones go") : "set aside" + (waits ? ", " + waits + " of them once their phones go" : "");
  } else if (kind.startsWith("lane:")) { const l = kind.slice(5); words = l ? "kept for " + laneWord(l) : "opened to either lane"; }
  else if (kind.startsWith("cap:")) { const c = +kind.slice(4); words = c ? "capped at " + c + " a day" : "uncapped"; }
  else if (kind === "test") words = "being tested; the address " + pl("it", "each") + " comes out at is read";
  else words = "removed to the archive";
  const one = who ? who + ": " + n + pl(" proxy", " proxies") : rows.length === 1 ? nameText(rows[0]) : n + pl(" proxy", " proxies");
  return [one + " " + words + "." + (no.length ? " " + stop(why) + rest : ""), !no.length];
}
/* The drawer is a modal: it takes the focus and the page holds still behind
   it; closed, the focus goes back to where it came from - or to the same
   proxy's name if that row was drawn again meanwhile. */
function closeLayer() {
  const a = el("layer").querySelector(".draw");
  el("layer").innerHTML = "";
  document.documentElement.classList.remove("locked");
  if (!a) return;
  const back = drawerFrom && drawerFrom.isConnected ? drawerFrom : document.querySelector('[data-open="' + a.dataset.id + '"]');
  drawerFrom = null;
  if (back) back.focus({preventScroll: true});
}
function openDrawer(id, from) {
  const e = EX.find(x => x.id === id);
  if (!e) return;
  if (!el("layer").querySelector(".draw")) { const a = from || document.activeElement; drawerFrom = a && a !== document.body ? a : null; }
  el("layer").innerHTML = '<div class="scrim"></div><aside class="draw opening" role="dialog" aria-modal="true" aria-label="' + esc(nameText(e)) + '" data-id="' + e.id + '">' + drawerHtml(e) + '</aside>';
  document.documentElement.classList.add("locked");
  el("layer").querySelector(".xbtn").focus({preventScroll: true});
  const a = el("layer").querySelector(".draw");
  setTimeout(() => a.classList.remove("opening"), 1200);
}
/* After an action from the drawer: the same drawer, redrawn where it stands
   and scrolled where it was; gone with its proxy. */
function redrawDrawer() {
  const a = el("layer").querySelector(".draw");
  if (!a) return;
  if (a.dataset.arch) return;
  const e = EX.find(x => x.id === +a.dataset.id);
  if (!e) { closeLayer(); return; }
  const top = a.querySelector(".body").scrollTop, had = a.contains(document.activeElement) && document.activeElement.dataset;
  a.classList.remove("opening");
  a.innerHTML = drawerHtml(e);
  // Keep the focus on the same door or switch after the redraw.
  if (had) { const same = had.menu ? a.querySelector('[data-menu="' + had.menu + '"]') : had.psw ? a.querySelector("[data-psw]") : null; (same || a.querySelector(".xbtn")).focus({preventScroll: true}); }
  a.querySelector(".body").scrollTop = top;
}
function drawerHtml(e) {
  const v = vOf(e), n = sum(v), us = e.u || [], rests = restsOf(e);
  const range = RANGES.find(r => r[0] === view.range)[1].toLowerCase();
  // What operators pressed on its phones: the three that matter as tiles.
  const kv = (i, c, w) => '<div class="kv ' + (n ? c : "na") + '"><b class="tab">' + (n ? pct(v[i], n) + "%" : "\u2014") + '</b><span>' + w + '</span><small class="tab">' + v[i] + ' of ' + n + '</small></div>';
  const pressed = n ? '<div class="kvs">' + kv(0, "d", "Done") + kv(1, "x", "Decline") + kv(2, "o", "OR") + '</div>' +
    '<div class="dmix">' + mixBar(v) + '<small>Auth <b class="tab">' + v[3] + '</b> \u00b7 Failed <b class="tab">' + v[4] + '</b></small></div>'
    : '<div class="rmore">No phone of this proxy was pressed ' + (view.range === "a" ? "yet" : range) + '.</div>';
  // Its rounds as a timeline, newest first, by day: each phone with how long
  // it held the proxy; between two phones, the rest the proxy had from the
  // one leaving to the next coming - amber under an hour.
  const SHOW = 14, holds = holdsOf(e);
  let day = "", ix = 0;
  const at = () => ' style="--i:' + (ix++) + '"';
  const tl = us.map((u, i) => ({k: i + 1, when: u[0], how: u[1], b: u[2], ip: u[3], serial: u[4] || "", by: u[5] || "", open: !u[6], hold: holds[i], rest: rests[i]})).reverse().slice(0, SHOW).map(r => {
    const head = r.when.slice(0, 10) !== day ? '<div class="rday"' + at() + '>' + esc(stamp(r.when.slice(0, 10))) + '</div>' : '';
    day = r.when.slice(0, 10);
    const cls = r.b || (r.how === 0 ? "no" : r.how === 2 ? "mv" : "");
    const held = r.how === 0 ? "" : r.open ? "still on it" : r.hold != null ? "held " + fmtH(r.hold) : "";
    const how = (r.how === 2 ? "moved here" : r.how === 1 ? "signed in" : "sign-in refused") + (r.serial || held ? '<small>' + esc([r.serial, held].filter(Boolean).join(" \u00b7 ")) + '</small>' : '') + (r.ip ? '<span class="nip">new IP</span>' : '');
    const out = r.b ? '<span class="out ' + r.b + '">' + WORD[BW[r.b]] + (r.by ? '<small>' + esc(r.by) + '</small>' : '') + '</span>'
      : '<span class="out none">' + (r.how === 0 ? "" : r.when < TRACK ? "not kept" : "no press") + '</span>';
    return head + '<div class="rrow ' + cls + '"' + at() + '><i class="dt"></i><b class="tab">R' + r.k + '</b><span class="t tab">' + r.when.slice(11) + '</span><span class="how' + (r.how === 0 ? " no" : r.how === 2 ? " mv" : "") + '">' + how + '</span>' + out + '</div>' +
      (r.rest != null ? '<div class="rgap' + (r.rest < 1 ? " short" : "") + '"' + at() + '><i></i><span>' + (r.rest < 1 / 60 ? "no rest" : "rested " + fmtH(r.rest)) + '</span></div>' : '');
  }).join("");
  const rs2 = rests.filter(x => x != null), rmed = median(rs2), hmed = median(holds), rmin = rs2.length > 1 ? Math.min(...rs2) : null;
  const rsum = us.length ? [us.length + ' use' + (us.length === 1 ? '' : 's'), hmed != null ? 'a phone holds it ' + fmtH(hmed) : '', rmed != null ? 'typical rest ' + fmtH(rmed) : '',
    rmin != null ? 'shortest ' + (rmin < 1 / 60 ? 'none' : fmtH(rmin)) : ''].filter(Boolean).join(' \u00b7 ') : '';
  // The details: where it comes out and how it is used, then how to reach it.
  const lines = [["Comes out at", e.ip ? e.ip + " \u00b7 " + (e.cc || "?") + (e.isp ? " \u00b7 " + e.isp : "") : "not read yet", 0],
    ["Today", e.today + " phone" + (e.today === 1 ? "" : "s") + (e.cap ? " of " + e.cap + " a day" : ", no cap"), 0],
    ["Added", (stamp(e.added) || "\u2014") + (e.batch ? " \u00b7 " + e.n : ""), 0],
    ["Endpoint", e.end, 1], ["Username", e.user || "none", e.user ? 1 : 0], ["Password", e.user ? "\u2022".repeat(14) : "none", 0], ["Type", "SOCKS5", 0]];
  const why = e.s === "phone" ? (e.serial ? "On phone " + e.serial : "On a phone") + (e.after ? "; set aside once that phone goes." : ".") : (e.note || "");
  return '<header><span class="id"><b>' + (e.batch ? esc([e.seller, e.type].filter(Boolean).join(" ")) + ' <i>#' + e.k + '</i>' : esc(e.n)) + '</b>' +
    '<span class="row">' + psw(e) + pill(e) + '<span class="lane">' + tile(e.lane, true) + '<b>' + laneWord(e.lane) + '</b></span>' + (e.batch ? '<span class="ty2">' + esc(stamp(e.added)) + '</span>' : '') + '</span>' +
    (why ? '<small class="why">' + esc(why) + '</small>' : '') + '</span>' +
    '<button class="xbtn" type="button" data-close="1" aria-label="Close">' + SVG(ICON.cross, 2.2) + '</button></header><div class="body">' +
    '<h3>What operators pressed \u00b7 ' + range + (n ? '<small>' + n + ' phone' + (n === 1 ? '' : 's') + '</small>' : '') + '</h3>' + pressed +
    '<h3>Rounds' + (rsum ? '<small>' + rsum + '</small>' : '') + '</h3>' +
    (us.length ? '<div class="rtl">' + tl + '</div>' + (us.length > SHOW ? '<div class="rmore">and ' + (us.length - SHOW) + ' earlier uses</div>' : '') : '<div class="rmore">Not used yet.</div>') +
    '<h3>Details</h3><div class="lines">' + lines.map(([k, val, c]) => '<div class="l1' + (c ? " copy" : "") + '"' + (c ? ' data-copy="' + esc(val) + '" role="button" tabindex="0"' : '') + '><span class="k">' + k + '</span><span class="v">' + esc(val) + '</span>' + (c ? '<span class="cp">' + SVG(ICON.copy, 1.8) + '</span>' : '<span></span>') + '</div>').join("") + '</div></div>' +
    '<footer>' + doors(e) + '</footer><span class="sr" role="status" aria-live="polite"></span>';
}
/* ------------------------------------------------------------- add proxies */
/* A line as vendors send it: host:port:user:pass, user:pass@host:port, or a
   URL with its scheme. The password is read and never drawn. The farm's
   phones take SOCKS5 only, so a line that names another type is left out. */
const okExit = x => x.port >= 1 && x.port <= 65535 && /[A-Za-z0-9]/.test(x.host) ? x : null;
function parseExit(line) {
  // scheme://[user[:pass]@]host:port - the scheme names the type
  let m = /^(socks5h?|socks4a?|https?):\/\/(?:([^:@\s\/]+)(?::(\S*))?@)?([^:@\s\/]+):(\d{1,5})$/i.exec(line);
  if (m) return okExit({type: /^http/i.test(m[1]) ? "HTTP" : /^socks4/i.test(m[1]) ? "SOCKS4" : "SOCKS5", host: m[4], port: +m[5], user: m[2] || "", pass: m[3] || ""});
  // host:port[:user:pass] - a password may hold ":" or "@"
  m = /^([^:@\s\/]+):(\d{1,5})(?::([^:\s]+):(\S+))?$/.exec(line);
  if (m) return okExit({type: "", host: m[1], port: +m[2], user: m[3] || "", pass: m[4] || ""});
  // user[:pass]@host:port - the last "@" splits
  m = /^([^:@\s\/]+)(?::(\S*))?@([^:@\s\/]+):(\d{1,5})$/.exec(line);
  if (m) return okExit({type: "", host: m[3], port: +m[4], user: m[1], pass: m[2] || ""});
  return null;
}
/* The line as the farm's reader takes it whatever the vendor's shape:
   user:pass@host:port, where the last "@" splits and the first ":" of the
   credentials does, so a password may hold either. */
const wire = x => (x.user ? x.user + ":" + x.pass + "@" : "") + x.host + ":" + x.port;
/* The paste, line by line: each read, and a line that is already in the
   pool, earlier in the paste, or of a type the phones cannot use, left out
   with that reason. The farm checks the pool again when it adds. */
function draftRows() {
  const seen = new Map(), pool = new Set(EX.map(e => e.ep).filter(Boolean));
  return el("paste").value.split(/\r?\n/).map((l, i) => ({raw: l.trim(), line: i + 1})).filter(r => r.raw).map(r => {
    const x = parseExit(r.raw), ep = x ? x.host.toLowerCase() + ":" + x.port + ":" + x.user : "";
    const why = !x ? "is not host:port:user:pass" : x.type && x.type !== "SOCKS5" ? "is " + x.type + " - the phones take SOCKS5 only"
      : pool.has(ep) ? "is already in the pool" : seen.has(ep) ? "repeats line " + seen.get(ep) : "";
    if (x && !why) seen.set(ep, r.line);
    return Object.assign(r, {x: why ? null : x, ep, why});
  });
}
/* Today in Tehran, as the farm last said: the day a new batch is stamped
   with, and the way it reads inside a name. */
const DAY = {iso: "", tag: "", word: ""};
const clean = s => String(s || "").replace(/[^A-Za-z0-9]/g, "");
/* A batch is its seller, its type and the day it came. Every proxy in it is
   named from the three and a number - Webshare-ISP-02Oct-1 - and a second
   paste of the same batch on the same day carries on the numbers. */
function batchOf() {
  const seller = prettySeller(clean(el("f-seller").value)), type = prettyType(clean(el("f-type").value));
  const prefix = [seller, type, DAY.tag].filter(Boolean).join("-");
  const key = (seller + "|" + type + "|" + DAY.tag).toLowerCase();
  const last = Math.max(issued[key] || 0, ...EX.filter(e => e.batch === key).map(e => e.k || 0));
  return {seller, type, prefix, key, last, label: [seller, type].filter(Boolean).join(" ") + " \u00b7 " + DAY.word};
}
/* Phones a day: any whole number, 0 for no cap. */
const capFrom = inp => Math.min(99, Math.max(0, parseInt(inp && inp.value, 10) || 0));
function capWord(box) {
  const inp = box.querySelector("input");
  inp.value = String(capFrom(inp));
  box.querySelector("small").textContent = +inp.value ? "a day" : "no cap";
}
let cmpOpen = false;
function renderRead() {
  const rows = draftRows(), good = rows.filter(r => r.x), bad = rows.length - good.length, SHOW = 6;
  const b = batchOf();
  let k = b.last;
  const box = el("cmp-read");
  box.hidden = !rows.length;
  box.innerHTML = !rows.length ? "" : '<span class="rh"><b class="ok">' + good.length + ' read</b>' + (bad ? '<b class="bad">' + bad + ' left out</b>' : '') +
    (!good.length ? '' : b.seller ? '<span class="to">' + esc(b.label) + '</span>' : '<span class="need">' + (el("f-seller").value.trim() ? "Write the seller in Latin letters or digits" : "Name the seller to name them") + '</span>') + '</span><ul>' +
    rows.slice(0, SHOW).map(r => r.x
      ? '<li class="ok">' + SVG(ICON.tick, 3) + '<span class="hp">' + esc(r.x.host + ":" + r.x.port) + '</span><span class="u">' + esc(r.x.user || "no login") + (r.x.type ? " \u00b7 " + r.x.type : "") + '</span><span class="to">' + (b.seller ? "#" + (++k) : "\u2014") + '</span></li>'
      : '<li class="bad">' + SVG(ICON.cross, 3) + '<span class="hp">' + esc(r.raw) + '</span><span class="u">line ' + r.line + ' ' + r.why + '</span></li>').join("") +
    (rows.length > SHOW ? '<li class="more">and ' + (rows.length - SHOW) + ' more</li>' : '') + '</ul>';
  const said = rows.length ? good.length + " read, " + bad + " left out" : "";
  if (el("cmp-sum").textContent !== said) el("cmp-sum").textContent = said;
  el("f-seller").classList.toggle("need", good.length > 0 && !b.seller);
  const go = el("cmp-add");
  go.disabled = !good.length || !b.seller || sending;
  go.innerHTML = SVG(ICON.plus, 2.2) + (sending ? "Sending\u2026" : good.length ? "Test and add " + good.length : "Test and add");
  // The button's quiet line says what waits in the card - only while the
  // card is closed, so the button does not grow under a person typing -
  // and a batch the farm is still testing.
  const draft = cmpOpen ? "" : [good.length && good.length + " waiting", bad && bad + " not read"].filter(Boolean).join(" \u00b7 ");
  const waiting = [adding && !cmpOpen ? "testing " + adding.n + "\u2026" : "", draft].filter(Boolean).join(" \u00b7 ");
  el("cmpc").classList.toggle("draft", !!draft);
  el("cmpc-sub").textContent = waiting || "paste a batch";
  el("cmpc").setAttribute("aria-label", "Add proxies" + (waiting ? ", " + waiting : ""));
}
function openCmp() {
  if (cmpOpen) return;
  cmpOpen = true;
  el("cmp").hidden = false;
  el("cmpc").setAttribute("aria-expanded", "true");
  renderRead();
  const ta = el("paste");
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}
function closeCmp(back) {
  if (!cmpOpen) return;
  cmpOpen = false;
  el("cmp").hidden = true;
  el("cmpc").setAttribute("aria-expanded", "false");
  renderRead();
  if (back) el("cmpc").focus();
}
/* Test and add: the lines read here go to the farm, which tests each and
   names them when it runs the add; the page watches it to its end. */
let adding = null, sending = false;
el("cmp").addEventListener("submit", ev => {
  ev.preventDefault();
  if (sending) return;
  const rows = draftRows(), good = rows.filter(r => r.x), bad = rows.length - good.length, b = batchOf();
  if (!good.length) return;
  if (!b.seller) { el("f-seller").focus(); return; }
  const lane = el("lanepick").querySelector('[aria-checked="true"]').dataset.pick;
  sending = true; renderRead();
  send("/pools/proxy/add-batch", {lines: good.map(r => wire(r.x)).join("\n"), seller: b.seller, type: b.type, lane, cap: capFrom(el("f-cap"))}).then(a => {
    sending = false;
    if (!a.ok) { renderRead(); say(a.note || "Nothing was added."); return; }
    // What was not understood stays in the field, to be mended or cleared.
    el("paste").value = rows.filter(r => !r.x).map(r => r.raw).join("\n");
    if (!bad) { el("f-seller").value = ""; el("f-type").value = ""; }
    adding = {n: good.length, label: b.label, key: b.key, bad, known: new Set(EX.map(e => e.id))};
    jobs.push({kind: "add", at: Date.now(), items: [{req: a.req, open: true}], add: adding});
    closeCmp(true); renderRead(); poll(1500);
    say("Testing " + good.length + (good.length === 1 ? " proxy" : " proxies") + " of " + b.label + " before " + (good.length === 1 ? "it joins" : "they join") +
      (bad ? "; " + bad + (bad === 1 ? " line was left out and waits" : " lines were left out and wait") + " in the field" : "") + ".", true);
  });
});
el("paste").addEventListener("input", renderRead);
el("f-seller").addEventListener("input", renderRead);
el("f-type").addEventListener("input", renderRead);
/* Typing into a stepper keeps it a whole number and its word right. */
document.addEventListener("input", ev => { if (ev.target.matches && ev.target.matches(".step input")) capWord(ev.target.parentElement); });
/* A paste while the closed field has focus opens it with the paste in. */
document.addEventListener("paste", ev => {
  if (cmpOpen || document.activeElement !== el("cmpc")) return;
  const text = ev.clipboardData && ev.clipboardData.getData("text");
  if (!text) return;
  ev.preventDefault();
  const ta = el("paste");
  ta.value = ta.value.trim() ? ta.value.replace(/\s*$/, "\n") + text : text;
  openCmp();
});

/* ----------------------------------------------------------------- events */
/* A switch slides under the finger first; the rows are drawn again after. */
function closeMenu(back) {
  if (!menu) return;
  menu = ""; renderPop();
  document.querySelectorAll(".doors button.on,.bm.on,.sortb.on").forEach(b => b.classList.remove("on"));
  document.querySelectorAll('[data-menu][aria-expanded="true"]').forEach(b => b.setAttribute("aria-expanded", "false"));
  if (back && menuFrom && menuFrom.isConnected) menuFrom.focus({preventScroll: true});
  menuFrom = null;
}
/* The list, its tools and the selection bar move together. */
function renderList() { renderTools(); renderRows(); renderSel(); }
/* A redraw replaces the buttons it draws; the focus goes back to the new
   button that stands for the same thing, in the drawer first. */
const FOCUS_KEYS = ["data-seg", "data-k", "data-flag", "data-tick", "data-psw", "data-bsw", "data-keep", "data-menu", "data-fam", "data-open", "data-clear"];
const focusKey = a => !a || a === document.body ? "" : FOCUS_KEYS.map(k => a.hasAttribute(k) ? "[" + k + '="' + a.getAttribute(k) + '"]' : "").join("");
function refocus(key) {
  if (!key) return;
  const d = el("layer").querySelector(".draw"), b = (d && d.querySelector(key)) || document.querySelector(key);
  if (b && b !== document.activeElement) b.focus({preventScroll: true});
}
function slide(sw, on, then) {
  const fk = focusKey(sw), had = document.activeElement === sw;
  sw.setAttribute("aria-checked", String(on)); sw.classList.remove("wait"); sw.disabled = true;
  setTimeout(() => { then(); if (had) refocus(fk); }, 190);
}
function onClick(ev) {
  const t = ev.target;
  if (menu && !t.closest(".pop") && !t.closest("[data-menu]")) closeMenu();
  if (cmpOpen && !t.closest("#addbox")) closeCmp();
  if (t.closest("#cmpc")) { openCmp(); return; }
  const cm = t.closest("[data-cmp]");
  if (cm) { if (cm.dataset.cmp === "clear") { el("paste").value = ""; renderRead(); el("paste").focus(); } else closeCmp(true); return; }
  const stp = t.closest("[data-step]");
  if (stp) { const box = stp.parentElement, inp = box.querySelector("input"); inp.value = String(capFrom(inp) + +stp.dataset.step); capWord(box); return; }
  if (t.classList.contains("scrim") || t.closest("[data-close]")) { closeLayer(); return; }
  const mn = t.closest("[data-menu]");
  if (mn) {
    if (menu === mn.dataset.menu) { closeMenu(); return; }
    closeMenu();
    menu = mn.dataset.menu; menuFrom = mn; mn.classList.add("on"); mn.setAttribute("aria-expanded", "true"); renderPop();
    // Opened from the keyboard, the menu takes the focus.
    if (!ev.detail) { const first = document.querySelector(".pop button,.pop input"); if (first) first.focus(); }
    return;
  }
  const sw = t.closest("[data-bsw]");
  if (sw) { const on = sw.getAttribute("aria-checked") !== "true"; slide(sw, on, () => setBatch(sw.dataset.bsw, on)); return; }
  const ps = t.closest("[data-psw]");
  if (ps) { if (ps.classList.contains("busy")) return; const on = ps.getAttribute("aria-checked") !== "true"; slide(ps, on, () => { apply(on ? "free" : "aside", new Set([+ps.dataset.psw])); redrawDrawer(); }); return; }
  const bd = t.closest("[data-bdo]");
  if (bd) {
    const from = menuFrom && menuFrom.getAttribute("data-menu");
    batchDo(bd.dataset.bdo, bd.dataset.key);
    if (from && !ev.detail) refocus('[data-menu="' + from + '"]');
    menuFrom = null;
    return;
  }
  const d = t.closest("[data-do]");
  if (d) {
    const id = +d.dataset.id, k = d.dataset.do, from = menuFrom && menuFrom.getAttribute("data-menu");
    if (k === "story") { const back = menuFrom; closeMenu(); openDrawer(id, back); return; }
    apply(k === "capset" ? "cap:" + capFrom(el("pop-cap")) : k, new Set([id]));
    redrawDrawer();
    // Chosen from the keyboard, the focus goes back to the door that opened the menu.
    if (from && !ev.detail) refocus('[data-menu="' + from + '"]');
    menuFrom = null;
    return;
  }
  const pick = t.closest("[data-pick]");
  if (pick && t.closest("#cmp")) { pick.parentElement.querySelectorAll("button").forEach(b => b.setAttribute("aria-checked", String(b === pick))); return; }
  const sg = t.closest("[data-seg]");
  if (sg) { view[sg.dataset.seg] = sg.dataset.k; renderAll(); return; }
  const sk = t.closest("[data-sortk]");
  if (sk) { view.sort = sk.dataset.sortk; view.dir = SORTS[view.sort].dir; closeMenu(); renderList(); if (!ev.detail) refocus('[data-menu="sort"]'); return; }
  const fg = t.closest("[data-flag]");
  if (fg) { view.flag = view.flag === fg.dataset.flag ? "" : fg.dataset.flag; renderList(); return; }
  const clr = t.closest("[data-clear]");
  if (clr) {
    if (clr.dataset.clear === "all") { Object.assign(view, {scope: "all", lane: "", flag: "", fam: "", q: ""}); el("q").value = ""; }
    else view.fam = "";
    renderFams(); renderRounds(); renderList(); return;
  }
  const fc = t.closest("[data-fam]");
  if (fc) { view.fam = view.fam === fc.dataset.fam ? "" : fc.dataset.fam; if (view.fam) view.scope = "all"; renderFams(); renderRounds(); renderList(); if (view.fam) el("s-ex").scrollIntoView({behavior: calm ? "auto" : "smooth", block: "start"}); return; }
  const bk = t.closest("[data-bulk]");
  if (bk) {
    if (bk.dataset.bulk === "clear") { ticked.clear(); renderRows(); renderSel(); }
    else if (bk.dataset.keep) { const kind = bk.dataset.bulk; slide(bk, kind === "free", () => apply(kind, new Set(ticked))); }
    else apply(bk.dataset.bulk, new Set(ticked), "", true);
    return;
  }
  const cp = t.closest("[data-copy]");
  if (cp) {
    (navigator.clipboard ? navigator.clipboard.writeText(cp.dataset.copy) : Promise.reject())
      .then(() => { cp.classList.add("just"); setTimeout(() => cp.classList.remove("just"), 1100); say("Copied.", true); })
      .catch(() => { const s = getSelection(), r = document.createRange(); r.selectNodeContents(cp.querySelector(".v")); s.removeAllRanges(); s.addRange(r); say("Select and copy \u2014 the clipboard is not open here."); });
    return;
  }
  const tk = t.closest("[data-tick]");
  if (tk) { const id = +tk.dataset.tick; ticked.has(id) ? ticked.delete(id) : ticked.add(id); renderRows(); renderSel(); return; }
  if (t.closest("#all")) { const rows = shown(); rows.every(e => ticked.has(e.id)) ? rows.forEach(e => ticked.delete(e.id)) : rows.forEach(e => ticked.add(e.id)); renderRows(); renderSel(); return; }
  const th = t.closest("th.sortable");
  if (th) { if (view.sort === th.dataset.sort) view.dir *= -1; else { view.sort = th.dataset.sort; view.dir = SORTS[th.dataset.sort].dir; } renderList(); return; }
  const open = t.closest("[data-open]");
  if (open) { openDrawer(+open.dataset.open); return; }
  if (t.closest("#see-arch")) { openArchive(t.closest("#see-arch")); return; }
}
document.addEventListener("click", ev => {
  const a = document.activeElement, fk = focusKey(a);
  onClick(ev);
  if (fk && a && !a.isConnected) refocus(fk);
});
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape") {
    if (cmpOpen) { closeCmp(true); return; }
    if (menu) { closeMenu(true); return; }
    closeLayer();
    return;
  }
  // Inside a menu the arrows walk its choices.
  const pop = ev.target.closest && ev.target.closest(".pop[role='menu']");
  if (pop && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
    ev.preventDefault();
    const bs = [...pop.querySelectorAll("button")], i = bs.indexOf(ev.target);
    bs[(i + (ev.key === "ArrowDown" ? 1 : bs.length - 1)) % bs.length].focus();
    return;
  }
  // In a radio group the arrows move the choice, as radio groups do.
  const radio = ev.target.matches && ev.target.matches("[role=radio]") && ev.target.closest("[role=radiogroup]");
  if (radio && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(ev.key)) {
    ev.preventDefault();
    const rs = [...radio.querySelectorAll("[role=radio]")], i = rs.indexOf(ev.target), step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : -1;
    const next = rs[(i + step + rs.length) % rs.length];
    next.focus(); next.dispatchEvent(new MouseEvent("click", {bubbles: true, detail: 0}));
    return;
  }
  // The drawer keeps Tab inside itself and its open menu, round and round.
  const draw = el("layer").querySelector(".draw");
  if (draw && ev.key === "Tab") {
    const f = [...draw.querySelectorAll("button,[tabindex='0'],input")].filter(x => !x.disabled && (x.offsetParent !== null || x.closest(".pop")));
    if (!f.length) return;
    ev.preventDefault();
    const i = f.indexOf(document.activeElement);
    f[i === -1 ? (ev.shiftKey ? f.length - 1 : 0) : (i + (ev.shiftKey ? -1 : 1) + f.length) % f.length].focus();
    return;
  }
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey) && cmpOpen) { ev.preventDefault(); el("cmp-add").click(); return; }
  if (ev.key === "Enter" && ev.target.id === "pop-cap") { ev.preventDefault(); const set = document.querySelector('[data-do="capset"],[data-bdo="capset"]'); if (set) set.click(); return; }
  // What is drawn as a button answers Enter and Space like one.
  if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches && ev.target.matches("[data-copy],[data-open],th.sortable")) { ev.preventDefault(); ev.target.click(); }
});
/* Any scroll - the page, or the table sideways - closes an open menu,
   unless someone is typing in its box: a phone's keyboard scrolls the page
   as it opens. */
document.addEventListener("scroll", () => {
  const typing = document.activeElement && document.activeElement.matches && document.activeElement.matches(".pop input");
  if (menu && !typing) closeMenu();
}, {capture: true, passive: true});
/* A menu belongs where its button was; a resize moves the button. */
window.addEventListener("resize", () => closeMenu());
el("q").addEventListener("input", ev => { view.q = ev.target.value.trim(); renderList(); });

/* ------------------------------------------------------------- the farm */
/* Every press goes to the farm and every answer comes back as JSON - the
   console's gates too (signed out, a stale session), by the header the
   Station's script sends. A press carries its own token, so the same press
   sent twice is one request and the next press is another. */
const REV = (document.querySelector('meta[name="gf-rev"]') || {}).content || "";
const HEAD = {"X-GF-Station": "page", "Accept": "application/json"};
let pressN = 0, busy = 0;
function send(path, fields) {
  const body = new URLSearchParams(Object.assign({csrf: el("gf-csrf").value, press: "px" + Date.now().toString(36) + (++pressN)}, fields));
  return fetch(path, {method: "POST", body, headers: HEAD, credentials: "same-origin", cache: "no-store"})
    .then(r => r.json().catch(() => ({ok: false, note: "The farm answered " + r.status + " - reload the page and look again."})))
    .then(a => { if (!a.ok && a.go) location.href = a.go; return a; })
    .catch(() => ({ok: false, note: "The farm did not answer - look at the list before pressing again."}));
}
function get(path, tag) {
  return fetch(path, {headers: Object.assign({}, HEAD, tag ? {"If-None-Match": tag} : {}), credentials: "same-origin", cache: "no-store"})
    .then(r => r.status === 304 ? {ok: true, same: true} : r.json().then(a => Object.assign(a, {etag: r.headers.get("ETag") || ""})).catch(() => ({ok: false})))
    .catch(() => ({ok: false}));
}
/* The farm's answer, taken in: the pool, the day and the ranges. Ticks and a
   batch filter outlive it; a proxy that left the pool leaves them. */
function load(d) {
  DATA = d;
  EX.length = 0;
  (d.exits || []).forEach(e => EX.push(e));
  archived = d.archived || 0;
  issued = d.issued || {};
  TRACK = d.track || "";
  Object.assign(SINCE, d.since || {});
  RANGE_WORD.a = d.trackWord ? "since " + d.trackWord : "so far";
  Object.assign(DAY, d.day || {});
  el("f-date").textContent = DAY.word;
  [...ticked].forEach(id => { if (!EX.some(e => e.id === id)) ticked.delete(id); });
  if (view.fam && !EX.some(e => famKey(e) === view.fam)) view.fam = "";
}
/* A state the page was not asked for - the farm moving on its own: drawn,
   and every proxy that changed glows once. */
const sig = e => [e.s, e.after, e.serial, e.today, e.lane, e.cap, e.ip, (e.u || []).length].join("|");
function take(d) {
  const was = new Map(EX.map(e => [e.id, sig(e)])), fk = focusKey(document.activeElement);
  load(d);
  EX.forEach(e => { if (was.get(e.id) !== sig(e)) flashIds.add(e.id); });
  renderAll(); redrawDrawer(); renderRead();
  refocus(fk);
  setTimeout(() => litIds.clear());
  litFam = "";
}
/* Requests the farm runs through its queue - turning a proxy on (it is
   tested first), Test, Add - are watched until they end: the page asks
   every two seconds while one is open, every twenty otherwise. A state that
   arrives while a menu is open or a press is on its way waits for it. */
const jobs = [], pend = new Map();
let pollTimer = 0, polling = false, etag = "", held = null, seq = 0, drawnAt = 0;
function poll(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(refresh, ms); }
const quiet = () => !menu && !busy && !document.querySelector(".sw:disabled");
const idle = () => quiet() && !cmpOpen && !el("paste").value.trim() && !el("layer").firstChild && !ticked.size && !jobs.length;
function watch(kind, items, who, rows) {
  const job = {kind: kind === "free" ? "free" : "test", who, rows, at: Date.now(), items: items.map(i => ({id: i.id, req: i.req, open: true}))};
  job.items.forEach(i => pend.set(i.id, job.kind));
  jobs.push(job);
  poll(1500);
}
function refresh() {
  if (polling) return;
  polling = true;
  const open = [...new Set(jobs.flatMap(j => j.items.filter(i => i.open).map(i => i.req)))], mine = ++seq;
  get("/pools/proxy/state" + (open.length ? "?req=" + open.join(",") : ""), open.length ? "" : etag).then(a => {
    polling = false;
    // Signed out meanwhile: to the sign-in, not a page that quietly stops.
    if (!a.ok && a.go) { location.href = a.go; return; }
    // Asked before a press answered: older than what the page shows.
    if (mine < drawnAt) { poll(1000); return; }
    if (a.ok && !a.same) {
      if (a.etag) etag = a.etag;
      if (a.rev && REV && a.rev !== REV && idle()) { location.reload(); return; }
      if (a.state) held = a.state;
      settle(a.reqs || {});
    }
    if (held && quiet()) {
      const d = held, ended = jobs.filter(j => j.items.every(i => !i.open));
      held = null; drawnAt = mine;
      ended.forEach(j => { jobs.splice(jobs.indexOf(j), 1); j.items.forEach(i => { if (i.id) pend.delete(i.id); }); });
      ended.forEach(j => mark(j, d));
      take(d);
      const said = ended.map(words);
      if (said.length) say(said.map(s => s[0]).join(" "), said.every(s => s[1]));
    }
    jobs.forEach(j => {
      if (!j.told && Date.now() - j.at > 180000) { j.told = true; say("The farm has not finished testing yet - Requests shows where it stands; this page keeps watching."); }
    });
    const slow = jobs.length && jobs.every(j => Date.now() - j.at > 180000);
    poll(held ? 1500 : jobs.length ? (slow ? 20000 : 2000) : document.hidden ? 60000 : 20000);
  });
}
function settle(reqs) {
  jobs.forEach(j => j.items.forEach(i => {
    const r = reqs[i.req];
    if (i.open && r && r.status !== "queued" && r.status !== "running") Object.assign(i, {open: false, ok: r.status === "done", note: r.result, r});
  }));
}
/* What an ended request changed glows once; a proxy that went back in play
   lights its switch, a new batch its card. */
function mark(job, d) {
  if (job.kind === "add") {
    d.exits.forEach(e => { if (!job.add.known.has(e.id)) flashIds.add(e.id); });
    flashFam = job.add.key; litFam = job.add.key;
    adding = null;
    return;
  }
  job.items.forEach(i => { flashIds.add(i.id); if (job.kind === "free" && i.ok) litIds.add(i.id); });
}
/* The sentence for an ended request: the farm's own for one proxy, in the
   page's words; a count for many. */
function words(job) {
  const its = job.items, good = its.filter(i => i.ok).length, bad = its.length - good;
  if (job.kind === "add") {
    const r = its[0].r || {}, a = job.add, n = (r.added || []).length, dead = (r.dead || []).length;
    if (!n) return [stop(cap1(r.result || "Nothing was added")), false];
    const ks = EX.filter(e => !a.known.has(e.id) && e.batch === a.key).map(e => e.k).sort((x, y) => x - y);
    return [a.label + (ks.length ? " #" + ks[0] + (ks.length > 1 ? " to #" + ks[ks.length - 1] : "") : "") + " tested and added" + (n - dead ? ", " + (n - dead) + " free" : "") +
      (dead ? ", " + dead + " did not answer and joined as dead" : "") + (r.skipped ? "; " + r.skipped + " already in the pool" : "") +
      (r.refused ? "; " + r.refused + " the farm could not read" : "") + ".", !dead && !r.refused];
  }
  if (its.length === 1) {
    const e = EX.find(x => x.id === its[0].id) || (job.rows || []).find(x => x.id === its[0].id);
    const note = inWords(e, its[0].note) || (e ? nameText(e) : "The proxy") + (its[0].ok ? " is done" : " did not go through");
    return [stop(cap1(note)), !!its[0].ok];
  }
  const one = job.who ? job.who + ": " : "";
  if (job.kind === "free") return [one + good + " back in play" + (bad ? ", " + bad + " did not answer and stay out" : "") + ".", !bad];
  return [one + good + " answered" + (bad ? ", " + bad + " did not" : "") + ".", !bad];
}
/* The archive: the removed proxies, newest first, in a drawer of its own. */
const archName = n => { const m = /^([A-Za-z0-9]+)(?:-([A-Za-z0-9]+))?-(\d{2})([A-Z][a-z]{2})-(\d+)$/.exec(n); return m ? [m[1], m[2]].filter(Boolean).join(" ") + " #" + m[5] + " \u00b7 " + (+m[3]) + " " + m[4] : n; };
function openArchive(from) {
  get("/pools/proxy/archive").then(a => {
    if (!a.ok) { say(a.note || "The archive could not be read just now."); return; }
    const rows = a.rows || [];
    if (!el("layer").querySelector(".draw")) drawerFrom = from || null;
    const head = !rows.length ? "Nothing has been removed yet." : (rows.length < archived ? "The newest " + rows.length + " of " + archived : "All " + rows.length) +
      ", newest first. A removed proxy keeps its name; its number is never given again.";
    el("layer").innerHTML = '<div class="scrim"></div><aside class="draw opening" role="dialog" aria-modal="true" aria-label="Removed proxies" data-arch="1">' +
      '<header><span class="id"><b>Removed proxies</b><small class="why">' + esc(head) + '</small></span><button class="xbtn" type="button" data-close="1" aria-label="Close">' + SVG(ICON.cross, 2.2) + '</button></header>' +
      '<div class="body"><div class="arl">' + (rows.length ? rows.map((r, i) => '<div style="--i:' + i + '"><b>' + esc(archName(r.n)) + '</b><span class="t">' + esc(stamp(r.at)) + '</span><small>' +
        esc([r.by && "by " + r.by, r.was && "was " + r.was, r.end].filter(Boolean).join(" \u00b7 ")) + '</small></div>').join("") : '<div class="none">Nothing yet.</div>') + '</div></div></aside>';
    document.documentElement.classList.add("locked");
    el("layer").querySelector(".xbtn").focus({preventScroll: true});
    const d = el("layer").querySelector(".draw");
    setTimeout(() => d.classList.remove("opening"), 1200);
  });
}

/* The page opens once: everything drawn, the ring's numbers counting up,
   and after the opening moment the page only answers presses - and the farm,
   which it asks every twenty seconds. */
function countUp(node, to, ms) {
  if (calm || !to) return;
  const t0 = performance.now();
  const step = now => { const k = Math.min(1, (now - t0) / ms); node.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
  node.textContent = "0";
  requestAnimationFrame(step);
  // A tab opened behind others gets no frames; the number still lands.
  setTimeout(() => { node.textContent = String(to); }, ms + 200);
}
load(DATA);
renderAll();
renderRead();
document.querySelectorAll(".donut .num, .lr b").forEach(b => countUp(b, +b.textContent, 900));
setTimeout(() => document.querySelector("main").classList.remove("boot"), 1500);
poll(20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(300); });
