/* The Gmails page (2026-10-07): the prototype the user approved, on the
   farm itself. Every address the pool holds with where it stands, every
   sign-in it has had - what Google said, how many captcha rounds, on which
   phone and through which proxy - and what the operator pressed on the
   phone it reached, on which product, and who. Batches are counted over
   the pool and the archive together, so a batch that is all spent is
   still judged. The farm sends whether a row carries a key or a recovery
   address, never the values: an admin's drawer asks for one Gmail's own.
   Every press goes to the farm, which moves a Gmail only if it still
   stands where this page drew it, and its Undo is the farm's too. */
let DATA = JSON.parse(document.getElementById("gf-state").textContent);

/* ------------------------------------------------------------- the keys */
/* Two fixed sets of words. Operators press five keys. Google's answers are
   many raw reasons, folded into seven categories so every number on the page
   is in the same words - read off three weeks of sign-ins (2026-10-02). The
   raw reason stays on each sign-in, with the stage Google answered at. */
const GK = ["gin", "gcap", "gtel", "gref", "gbad", "gmiss", "gnone"];
const GW = {gin: "In", gcap: "Captcha", gtel: "Phone asked", gref: "Refused", gbad: "Wrong details", gmiss: "Missing 2FA", gnone: "No verdict"};
const GLOW = {gin: "let in", gcap: "captcha", gtel: "phone asked", gref: "refused", gbad: "wrong details", gmiss: "missing 2FA", gnone: "no verdict"};
/* One short sentence each: what the category means. What it holds is
   listed under it, so the sentence does not list it again. */
const GMEAN = {gin: "Google let the address in.",
  gcap: "Picture puzzles, round after round, until the run gave up.",
  gtel: "Google wanted a phone number before letting the address in.",
  gref: "Google refused the sign-in outright.",
  gbad: "The password or the key the seller gave does not work.",
  gmiss: "Google asked for a second step the row cannot give.",
  gnone: "Nothing was decided about the address."};
const VK = ["kd", "kx", "ko", "ka", "kf"];
const VW = {kd: "Done", kx: "Decline", ko: "OR", ka: "Auth", kf: "Failed"};
const VOF = {d: "kd", x: "kx", o: "ko", a: "ka", f: "kf"};
const VB = {done: "kd", decline: "kx", or: "ko", auth: "ka", failed: "kf"};
/* The products a Gmail is spent on are the Station's lanes: GPT (ChatGPT
   and Claude) and Spotify. Every press carries the lane of its phone, so
   each Gmail ends with who pressed what on which. A batch is for any
   product unless it is kept for one, and a build for the other product
   passes its Gmails by. A product is drawn as its app tile, as on the
   Station and the Proxies. A phone built by hand for another app (the
   Station's Other) is named only on its own Gmail, never counted beside
   the two: one press in all the records (the user: "seems extra"). */
const PK = ["gpt", "spotify"], PL = ["gpt", "spotify", "other"];
const PW = {gpt: "GPT", spotify: "Spotify", other: "Other app"};
const PX = {gpt: "spotify", spotify: "gpt"};
const LOGO = {
 gpt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M22.28 9.82a5.98 5.98 0 0 0-.51-4.91 6.05 6.05 0 0 0-6.51-2.9A6.07 6.07 0 0 0 4.98 4.18a5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .75 7.1 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9A5.98 5.98 0 0 0 13.26 24a6.06 6.06 0 0 0 5.77-4.21 5.99 5.99 0 0 0 4-2.9 6.06 6.06 0 0 0-.75-7.07z' +
  'm-9.02 12.61a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.79.79 0 0 0 .39-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.49 4.5zM3.6 18.3a4.47 4.47 0 0 1-.54-3.01l.15.08 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.34a.08.08 0 0 1-.03.06L9.74 19.95a4.5 4.5 0 0 1-6.14-1.65z' +
  'M2.34 7.9a4.49 4.49 0 0 1 2.37-1.98V11.6a.77.77 0 0 0 .39.68l5.81 3.35-2.02 1.17a.08.08 0 0 1-.07 0L4 14.02A4.5 4.5 0 0 1 2.34 7.87zm16.6 3.86L13.1 8.36l2.02-1.16a.08.08 0 0 1 .07 0l4.83 2.79a4.49 4.49 0 0 1-.68 8.1v-5.67a.79.79 0 0 0-.4-.67z' +
  'm2.01-3.03l-.14-.08-4.78-2.78a.78.78 0 0 0-.78 0L9.41 9.23V6.9a.07.07 0 0 1 .03-.06l4.83-2.79a4.5 4.5 0 0 1 6.68 4.66zM8.31 12.86l-2.02-1.16a.08.08 0 0 1-.04-.06V6.07a4.5 4.5 0 0 1 7.38-3.45l-.14.08-4.78 2.76a.79.79 0 0 0-.4.68zm1.1-2.36l2.6-1.5 2.61 1.5v3l-2.6 1.5-2.6-1.5z"/></svg>',
 spotify: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.52 17.34c-.24.36-.66.48-1.02.24-2.82-1.74-6.36-2.1-10.56-1.14-.42.12-.78-.18-.9-.54-.12-.42.18-.78.54-.9 4.56-1.02 8.52-.6 11.64 1.32.42.18.48.66.3 1.02z' +
  'm1.44-3.3c-.3.42-.84.6-1.26.3-3.24-1.98-8.16-2.58-11.94-1.38-.48.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.14C9.6 9.9 15 10.56 18.72 12.84c.36.18.54.78.24 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.3c-.6.18-1.2-.18-1.38-.72-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.72 1.62.54.3.72 1.02.42 1.56-.3.42-1.02.6-1.56.3z"/></svg>',
 // The Station's Other tile: its blue, and the Build cross.
 other: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>',
};
/* A product's tile; with no product, both on blue - for any. */
const tile = (l, size) => '<span class="app ' + (l || "any") + (size ? " " + size : "") + '" aria-hidden="true">' +
  (l ? LOGO[l] : '<span class="pair">' + LOGO.gpt + LOGO.spotify + '</span>') + '</span>';
/* Each raw reason in a few words - Google's own where it said them, short
   enough for a line of the list. */
const RAW = {signed_in: "signed in", captcha_shown: "picture puzzles", captcha_text: "a text or audio puzzle only",
  phone_verification_required: "asked for a phone number",
  // Both pages are headed "Couldn't sign you in"; the second is told by its
  // own line (tests/fixtures/google-verification-blocked.xml).
  sign_in_refused: "“Couldn’t sign you in”", verification_blocked: "“You didn’t provide enough info”",
  // Out of the queue whatever its status said: on the seller's list with no
  // reason kept, or details the farm cannot read.
  held_out: "kept out of the queue", unreadable: "the farm cannot read its details",
  unknown: "the run did not say",
  account_disabled: "the account is disabled", too_many_attempts: "“Too many failed attempts”",
  password_changed: "“Your password was changed”", wrong_password: "“Wrong password”",
  wrong_2fa_code: "the key’s codes were rejected", email_not_found: "“Couldn’t find your account”",
  no_authenticator: "a code, and no key on the row", no_authenticator_option: "no authenticator offered",
  no_recovery_email: "asked for the recovery address", owner_device_prompt: "a tap on the owner’s phone",
  stuck_on_2fa_push_to_other_device: "a prompt on another device",
  stuck_on_loading: "a page never finished loading", stuck_on_transient_error: "“Something went wrong”",
  unknown_screen: "a page the farm does not know", recaptcha_unreachable: "the puzzle never loaded",
  budget_exhausted: "the run ran out of time", screen_unreadable: "the screen could not be read",
  stuck_on_dismissable: "stuck on a dialog", stuck_on_2fa_code_entry: "stuck at the code box"};
/* Which category a raw reason folds into; anything unnamed is no verdict. */
const GKEY = {captcha_shown: "gcap",
  phone_verification_required: "gtel", stuck_on_2fa_verify_phone: "gtel",
  sign_in_refused: "gref", verification_blocked: "gref", account_disabled: "gref", too_many_attempts: "gref",
  wrong_password: "gbad", password_changed: "gbad", wrong_2fa_code: "gbad", email_not_found: "gbad",
  no_authenticator: "gmiss", no_authenticator_option: "gmiss", no_recovery_email: "gmiss",
  owner_device_prompt: "gmiss", stuck_on_2fa_push_to_other_device: "gmiss"};
const gkey = (reason, ok) => ok || reason === "signed_in" ? "gin" : GKEY[reason] || "gnone";
/* Where Google answered a sign-in: before the password, after it, or after
   the authenticator code went in. Let in, it is how: with the code, or on
   the password alone. */
const STAGE = {a: "before the password", p: "after the password", c: "after the code"};
const STAGE_IN = {c: "with the code", p: "on the password alone"};
const stageOf = u => !u || !u[7] ? "" : u[1] ? STAGE_IN[u[7]] || "" : STAGE[u[7]] || "";
/* A sign-in's answer in words: the stage where that is the telling part,
   the raw reason otherwise. */
const answerWords = u => { const k = gkey(u[2], u[1]); return k === "gin" || k === "gtel" ? stageOf(u) : k === "gcap" ? (u[7] && u[7] !== "a" ? stageOf(u) : "") : rawWord(u[2]); };
const rawWord = r => RAW[r] || (r ? r.replace(/^stuck_on_/, "stalled on ").replace(/_/g, " ") : "");
const gfold = m => { const v = GK.map(() => 0); Object.entries(m || {}).forEach(([r, n]) => { v[GK.indexOf(gkey(r, false))] += n; }); return v; };
const vfold = m => { const v = VK.map(() => 0); Object.entries(m || {}).forEach(([b, n]) => { if (VB[b]) v[VK.indexOf(VB[b])] += n; }); return v; };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const RANGES = [["t", "Today"], ["d", "3 days"], ["a", "All"]];
const RANGE_WORD = {t: "today", d: "in the last 3 days", a: "so far"};
const view = {range: "a", scope: "", flag: "", fam: "", q: "", sort: "state", dir: 1, gk: "", vk: "", prod: "", pv: "", old: false, defs: false};
const ticked = new Set();
let menu = "", noteTimer = 0, menuFrom = null, drawerFrom = null;
const flashIds = new Set(), litIds = new Set();
let flashFam = "", litFam = "";
let archived = 0;
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
/* The pool as the farm last sent it - refilled in place by `load`. */
const GM = [];

const el = id => document.getElementById(id);
const esc = t => String(t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const sum = a => a.reduce((x, y) => x + y, 0);
const pct = (a, b) => b ? Math.round(a / b * 100) : null;
const SVG = (d, w) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 2.1) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
const ICON = {
 tick: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', cross: '<path d="M7 7l10 10M17 7L7 17"/>',
 phone: '<rect x="6.5" y="2.5" width="11" height="19" rx="2.6"/><path d="M11 18.5h2"/>',
 plus: '<path d="M12 5v14M5 12h14"/>', copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
 more: '<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
 trash: '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6 7l1 13h10l1-13"/>',
 list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
 chev: '<path d="M6 9l6 6 6-6"/>',
 back: '<path d="M9 14l-4-4 4-4"/><path d="M5 10h9.5a4.5 4.5 0 0 1 0 9H12"/>',
 pause: '<rect x="6.5" y="5" width="3.5" height="14" rx="1"/><rect x="14" y="5" width="3.5" height="14" rx="1"/>',
 fix: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
 edit: '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
 mail: '<rect x="3" y="5.5" width="18" height="13" rx="2.6"/><path d="M4 7.5l8 6 8-6"/>',
 box: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M10 13h4"/>',
};

/* --------------------------------------------------------- where it stands */
/* A Gmail's places: free in the queue; waiting - refused, back in the queue
   at a set time; on a phone; stopped - three tries spent, or a reason no
   retry mends; set aside by hand; spent. */
const LADDER = 3;
const stateOf = e => e.st === "used" ? "spent" : (e.st === "ready" || e.st === "in_use") ? "phone"
  : e.st === "set_aside" ? "aside" : !e.st ? "free" : e.next ? "waiting" : "stopped";
const STATE = {free: "Free", waiting: "Waiting", phone: "On a phone", stopped: "Stopped", aside: "Set aside", spent: "Spent"};
const ORDER = {free: 0, waiting: 1, stopped: 2, aside: 3, phone: 4, spent: 5};
const inPlay = e => stateOf(e) === "free" || stateOf(e) === "waiting";
/* Not used yet: can be switched in and out of the queue and kept for a product. */
const canSwitch = e => ["free", "waiting", "stopped", "aside"].includes(stateOf(e));
/* Archive takes any Gmail no phone is behind: an unused one, or a spent
   one (its phone is gone) - the archive keeps it, and Undo puts it back. */
const canRemove = e => stateOf(e) !== "phone";
const isOut = e => stateOf(e) === "stopped" || stateOf(e) === "aside";
const SCOPE = {free: e => stateOf(e) === "free", waiting: e => stateOf(e) === "waiting", phone: e => stateOf(e) === "phone",
  out: isOut, spent: e => stateOf(e) === "spent"};
const SCOPE_WORD = {free: "Free", waiting: "Waiting", phone: "On phones", out: "Out of the queue", spent: "Spent"};
/* The sign-ins that count now: those since it was last marked fixed - a
   fixed Gmail starts its tries again, and the earlier ones stay in its
   story. */
const triesNow = e => { const t = e.t || []; return e.back ? t.filter(u => u[0] > e.back.at) : t; };
/* A Gmail Google refused, once mended by the seller or by hand, is marked
   fixed: it goes back to the pool as fresh stock. */
const canMend = e => canSwitch(e) && !unreadable(e) && (isOut(e) || triesNow(e).some(u => !u[1]));
/* Details the farm cannot read: nothing takes it, turned on or not, and a
   fix is an edit - the switch and Mark as fixed are not offered. */
const unreadable = e => e.st === "unreadable";
const lastTry = e => { const t = triesNow(e); return t[t.length - 1] || null; };
const lastPress = e => (e.p || [])[(e.p || []).length - 1] || null;
/* Google's last answer: the last sign-in is the authority (the row's own
   `last_reason` is sticky - an address refused on Monday and let in on
   Tuesday still carries Monday's word); a row never tried has only the
   word on it, if any, and one marked fixed has none yet. */
const answerOf = e => {
  const t = lastTry(e);
  if (t) return {k: gkey(t[2], t[1]), raw: t[1] ? "signed_in" : t[2], t};
  if (e.back) return null;
  const r = e.why || (["used", "ready", "in_use", "set_aside"].includes(e.st) ? "" : e.st);
  return r ? {k: gkey(r, false), raw: r, t: null} : null;
};
const pressOf = e => { const p = lastPress(e); return p ? VOF[p[1]] : ""; };
/* The product a Gmail went to: its press's, or the phone's it is on now. */
const prodOf = e => { const p = lastPress(e); return p ? p[4] || "gpt" : stateOf(e) === "phone" ? e.on || "" : ""; };
/* The product a Gmail is kept for, while it can still be used. */
const keptFor = e => canSwitch(e) ? e.for || "" : "";
const stamp = s => { const m = /(\d{4})-(\d\d)-(\d\d)(?: (\d\d:\d\d))?/.exec(s || ""); return m ? (+m[3]) + " " + MONTHS[+m[2] - 1] + (m[4] ? " " + m[4] : "") : ""; };
const day = s => stamp((s || "").slice(0, 10));
/* Two days as one short span: "1–2 Oct", "29 Sep – 1 Oct", or one day. */
const span = (a, z) => { if (!a || !z || a === z) return day(z || a); const x = day(a).split(" "), y = day(z).split(" "); return x[1] === y[1] ? x[0] + "–" + y[0] + " " + y[1] : day(a) + " – " + day(z); };
/* The page's own clock, in Tehran's wall-clock words like every time the
   farm sends ("YYYY-MM-DD HH:MM"): Iran keeps UTC+3:30 all year, so the
   times compare as strings and subtract as if they were UTC. */
const tehran = () => new Date(Date.now() + 210 * 6e4).toISOString().slice(0, 16).replace("T", " ");
let NOWS = tehran();
const when = s => (s || "").slice(0, 10) === NOWS.slice(0, 10) ? (s || "").slice(11, 16) : day(s);
const tOf = w => w ? Date.UTC(+w.slice(0, 4), +w.slice(5, 7) - 1, +w.slice(8, 10), +w.slice(11, 13) || 0, +w.slice(14, 16) || 0) : NaN;
let NOW = tOf(NOWS);
const tick = () => { NOWS = tehran(); NOW = tOf(NOWS); };
const fmtH = h => h == null ? "" : h < 1 ? Math.max(1, Math.round(h * 60)) + " min" : h < 10 ? (Math.round(h * 10) / 10) + " h" : h < 48 ? Math.round(h) + " h" : Math.round(h / 24) + " d";
const nth = n => n + (n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th");
const plural = (n, one, many) => n + " " + (n === 1 ? one : many || one + "s");

/* ------------------------------------------------------------- the stock */
function pace() {
  const days = (DATA.days || []).filter(d => d[0] < NOWS.slice(0, 10)).slice(-3);
  return days.length ? sum(days.map(d => d[2])) / days.length / 24 : 0;
}
/* The line has two parts. Above, the numbers - each a place an address can
   be, each a filter. The four the pool holds now sit each in a ring of
   what it is made of, the parts in their colours a hair apart: the free
   ones never tried and refused before, the waiting and the stopped by
   Google's answer, the phones signed in and still signing in. Past a
   hairline, the spent, with a bar a day of Google's let-ins - the seven
   days before, and today lit. Every part's key sits under its picture. */
const DIM = c => "color-mix(in srgb," + c + " 36%,transparent)";
/* A ring of shares, drawn from twelve o'clock: each part [n, key class,
   colour] an arc in its own slot of the circle, the slot its share, and
   the arc drawn short of the slot's ends by the gap - which counts the
   round caps - so the parts sit a hair apart and each still looks its
   size, however many there are. */
const RGAP = 3.9;
/* With `taps`, each part also gets a wide invisible ring over its arc for
   the pointer to land on - the arc itself is a few pixels thin. */
function ring(parts, taps) {
  const live = parts.filter(p => p[0] > 0), total = sum(live.map(p => p[0])), gap = live.length > 1 ? RGAP : 0;
  let at = 0, hit = "";
  return '<svg class="ring" viewBox="0 0 36 36" aria-hidden="true"><circle class="bg" r="15.9155" cx="18" cy="18"/>' + live.map(([n, cls, c]) => {
    const slot = n / total * 100, len = Math.max(0.01, slot - gap);
    const dash = 'stroke-dasharray:' + len.toFixed(2) + ' ' + (100 - len).toFixed(2) + ';stroke-dashoffset:' + (-(at + gap / 2)).toFixed(2);
    if (taps) hit += '<circle class="tap" data-k="' + cls + '" data-c="' + n + '" r="15.9155" cx="18" cy="18" style="' + dash + '"/>';
    at += slot;
    return '<circle class="arc ' + (cls || "") + '" data-k="' + (cls || "") + '" r="15.9155" cx="18" cy="18" style="' + (c ? "--kc:" + c + ";" : "") + dash + '"/>';
  }).join("") + hit + '</svg>';
}
function renderStock() {
  // What each number said before, so a changed one moves to its new value.
  const was = {};
  document.querySelectorAll(".num").forEach(b => { was[b.dataset.scope] = +b.querySelector(".dial b").textContent; });
  const by = {free: [], waiting: [], phone: [], out: [], spent: []};
  GM.forEach(e => { const s = stateOf(e); by[s === "stopped" || s === "aside" ? "out" : s].push(e); });
  const perHour = pace(), nFree = by.free.length, runway = perHour > 0 ? nFree / perHour : null;
  const hurt = runway == null ? "" : runway < 0.5 ? " run-out" : runway < 2 ? " run-low" : "";
  // The free ones, each counted once: never tried, marked fixed and not tried
  // since, and refused before - the three add up to the free number.
  const mended = by.free.filter(e => e.back && !triesNow(e).length).length;
  const fresh = by.free.filter(e => !e.back && !triesNow(e).length).length, back = nFree - fresh - mended;
  // Why the waiting and the stopped were refused, in the answer keys, most first.
  const why = list => GK.map(k => [list.filter(e => (answerOf(e) || {}).k === k).length, k])
    .concat([[list.filter(e => !answerOf(e) && !e.back).length, "bare"], [list.filter(e => !answerOf(e) && e.back).length, "barefix"]])
    .filter(x => x[0]).sort((a, b) => b[0] - a[0]);
  const lab = k => k === "bare" ? "set aside, never tried" : k === "barefix" ? "set aside, not tried since a fix" : GLOW[k];
  // A ring of reasons keeps its two largest and folds the rest into one
  // quiet part, so it never breaks into crumbs; the key names what was folded.
  const fold = list => list.length <= 3 ? list : list.slice(0, 2).concat([[sum(list.slice(2).map(x => x[0])), "", list.slice(2)]]);
  const wWhy = fold(why(by.waiting)), oWhy = fold(why(by.out)), REST = "#56627a";
  // A phone's Gmail is claimed before its build signs it in (`in_use`), and signed in after (`ready`).
  const signing = by.phone.filter(e => e.st === "in_use").length, onPhone = by.phone.length - signing;
  const today = NOWS.slice(0, 10), nextBack = by.waiting.map(e => e.next).filter(Boolean).sort()[0];
  const inToday = ((DATA.days || []).find(d => d[0] === today) || [today, 0, 0])[2];
  const week = (DATA.days || []).filter(d => d[0] < today).slice(-7).map(d => d[2]);
  const perDay = week.length ? Math.round(sum(week) / week.length) : 0, bars = week.concat([inToday]), tallest = Math.max(1, ...bars);
  const it = (c, words, cls) => '<span class="it"><i class="mk' + (cls ? " " + cls : "") + '"' + (c ? ' style="--kc:' + c + '"' : "") + ' aria-hidden="true"></i>' + words + '</span>';
  const quiet = words => '<span class="it q">' + words + '</span>';
  // The folded ones wrap between reasons, never inside one.
  const reasons = list => list.map(([n, k, rest]) => rest ? it(REST, n + " other") + quiet(rest.map(([m, r]) => '<span class="nw">' + m + " " + lab(r) + '</span>').join(" · "))
    : k.startsWith("bare") ? it("var(--quiet)", n + " " + lab(k)) : it("", n + " " + lab(k), k)).join("");
  const parts = list => list.map(([n, k, rest]) => rest ? [n, "", REST] : k.startsWith("bare") ? [n, "", "var(--quiet)"] : [n, k]);
  const pic = {
    free: [ring([[fresh, "", "var(--sc)"], [mended, "", "var(--fix)"], [back, "", DIM("var(--sc)")]]),
      !nFree ? quiet("the next build has nothing to take")
        : (fresh ? it("var(--sc)", fresh + " never tried") : "") + (mended ? it("var(--fix)", mended + " marked fixed") : "") +
          (back ? it(DIM("var(--sc)"), back + " refused before") : "") +
          // Kept for one product, a free Gmail is not there for the other's builds.
          PK.map(l => { const n = by.free.filter(e => e.for === l).length; return n ? quiet(n + " kept for " + PW[l]) : ""; }).join("")],
    waiting: [ring(parts(wWhy)),
      reasons(wWhy) + (nextBack ? quiet("back in the queue from " + (nextBack.slice(0, 10) === today ? nextBack.slice(11, 16) : stamp(nextBack))) : "")],
    phone: [ring([[onPhone, "", "var(--blue)"], [signing, "", DIM("var(--blue)")]]),
      !by.phone.length ? quiet("none right now")
        : (onPhone ? it("var(--blue)", onPhone + " signed in") : "") + (signing ? it(DIM("var(--blue)"), signing + " signing in now") : "")],
    out: [ring(parts(oWhy)), oWhy.length ? reasons(oWhy) : quiet("none")],
    spent: ['<span class="days" aria-hidden="true">' + bars.map((v, i) => '<i class="' + (i === bars.length - 1 ? "now" : "") + '" style="height:' +
        (v ? Math.max(6, v / tallest * 100) : 0).toFixed(1) + '%;--i:' + i + '"></i>').join("") + '</span>',
      it("var(--green)", inToday + " today", "vbar") + (week.length ? it(DIM("var(--green)"), perDay + " a day, past " + plural(week.length, "day"), "vbar") : "")],
  };
  const nums = [["free", "free"], ["waiting", "waiting"], ["phone", "on phones"], ["out", "out of the queue"], ["spent", "spent"]]
    .filter(([k]) => k !== "waiting" || by.waiting.length);
  el("stock").innerHTML = '<div class="nums">' + nums.map(([k, w], u) =>
    '<button class="num ' + k + (k === "free" ? hurt : "") + (view.scope === k ? " on" : "") + (by[k].length ? "" : " zero") + '" type="button" data-scope="' + k + '" aria-pressed="' + (view.scope === k) + '" style="--u:' + u + '">' +
    '<span class="dial">' + (k === "spent" ? '<b class="tab">' + by[k].length + '</b>' + pic[k][0] : pic[k][0] + '<b class="tab">' + by[k].length + '</b>') + '</span>' +
    '<span class="w">' + w + '</span><small class="cap">' + pic[k][1] + '</small></button>').join("") + '</div>';
  el("stockwhen").textContent = "as of " + stamp(asOf) + " Tehran";
  if (!document.querySelector("main.boot")) document.querySelectorAll(".num").forEach(b => {
    const k = b.dataset.scope, to = by[k].length;
    if (was[k] == null || was[k] === to) return;
    b.classList.add("moved");
    countUp(b.querySelector(".dial b"), to, 600, was[k]);
  });
}
/* --------------------------------------------------------- what happened */
/* A row of choices, one on; a choice may carry how many it holds. */
function seg(nm, label, opts, cur) {
  return '<span class="seg" role="radiogroup" aria-label="' + label + '">' + opts.map(([k, w, n]) =>
    '<button type="button" role="radio" aria-checked="' + (cur === k) + '" tabindex="' + (cur === k ? 0 : -1) + '" data-seg="' + nm + '" data-k="' + k + '">' + w + (n != null ? '<i class="tab">' + n + '</i>' : '') + '</button>').join("") + '</span>';
}
const sbar = (side, keys, v) => sum(v) ? '<span class="sbar ' + side + '" aria-hidden="true">' + keys.map((k, i) => v[i] ? '<i class="' + k + '" style="flex:' + v[i] + '"></i>' : "").join("") + '</span>'
  : '<span class="sbar none" aria-hidden="true"></span>';
/* A lane: its one number large at the left - the share of the key pointed
   at or pressed, let in and Done unless another is, in that key's colour -
   then the bar of every share, the total, and the keys that are both the
   bar's legend and the list's filter. */
function lane(label, side, keys, words, low, v, total, prods) {
  const n = sum(v), active = side === "g" ? view.gk : view.vk;
  return '<div class="ln"><div class="fig hero" aria-hidden="true">' + keys.map((k, i) =>
      '<span class="v ' + k + (n ? '' : ' na') + '"><b class="tab">' + (n ? pct(v[i], n) + '%' : '—') + '</b><span>' + low[k] + '</span></span>').join('') +
      '<small>' + label + '</small></div>' + sbar(side, keys, v) + '<span class="tot">' + total + '</span>' +
    '<span class="keys" role="group" aria-label="' + label + ': show only">' + keys.map((k, i) =>
      '<button type="button" class="key ' + k + (v[i] ? "" : " zero") + '" data-side="' + side + '" data-key="' + k + '" aria-pressed="' + (active === k) + '" title="Show only the Gmails whose last ' + (side === "g" ? "answer" : "press") + ' was ' + words[k] + '"><i></i>' + words[k] + ' <b class="tab">' + (n ? pct(v[i], n) + "%" : "\u2014") + '</b></button>').join("") +
    // Past a hairline, the products the phones were on, each with how many:
    // pointing at one puts only its presses on the lane, pressing it shows
    // only the Gmails used on it.
    (prods ? '<span class="ksep" aria-hidden="true"></span>' + prods.map(([l, m]) =>
      '<button type="button" class="key pk p-' + l + (m ? "" : " zero") + '" data-prod="' + l + '" aria-pressed="' + (view.prod === l) + '" title="Show only the Gmails used on ' + PW[l] + '">' +
      tile(l, "xs") + PW[l] + ' <b class="tab">' + m + '</b></button>').join("") : '') + '</span></div>';
}
const whatWas = {h: ""};
/* What a category holds in the range: every response folded into it with
   its count - split by the stage Google answered at, where the stage is the
   telling part (how it got in, where the phone was asked for, where the
   puzzles came). */
const BY_STAGE = ["signed_in", "phone_verification_required", "captcha_shown"];
function holds(k) {
  const g = DATA.G[view.range] || {}, gs = (DATA.GS || {})[view.range] || {}, out = [];
  Object.entries(g).filter(([r]) => gkey(r, false) === k).sort((x, y) => y[1] - x[1]).forEach(([r, n]) => {
    const by = BY_STAGE.includes(r) ? Object.entries(gs[r] || {}).filter(([s]) => s).sort((x, y) => y[1] - x[1]) : [];
    if (!by.length) { out.push([n, rawWord(r)]); return; }
    by.forEach(([s, m]) => out.push([m, r === "signed_in" ? STAGE_IN[s] : STAGE[s]]));
    const rest = n - sum(by.map(x => x[1]));
    if (rest > 0) out.push([rest, "stage not read"]);
  });
  return out;
}
function renderWhat() {
  el("rangeseg").innerHTML = seg("range", "Range", RANGES, view.range);
  const g = gfold(DATA.G[view.range]), gn = sum(g), t = DATA.bytry || [];
  // The operators' presses on every product - or on the one pointed at or pressed.
  const vp = (DATA.VP || {})[view.range] || {}, pon = view.pv || view.prod;
  const v = pon ? vfold(vp[pon]) : vfold(DATA.V[view.range]), vn = sum(v);
  const prods = PK.map(l => [l, sum(vfold(vp[l]))]).filter(x => x[1] || x[0] === view.prod);
  const html = lane("Google", "g", GK, GW, GLOW, g, plural(gn, "sign-in")) +
    lane("Operators" + (pon ? " · " + PW[pon] : ""), "v", VK, VW, VW, v, plural(vn, pon ? PW[pon] + " phone" : "phone"), prods) +
    '<button class="link defs-t" type="button" data-defs="1" aria-expanded="' + view.defs + '">' + (view.defs ? "Hide what each answer holds" : "What each answer holds") + '</button>' +
    (view.defs ? '<div class="defs">' + GK.map(k => { const h = holds(k); return '<p class="' + k + '"><b>' + GW[k] + '</b>' + GMEAN[k] +
      (h.length ? '<span class="hold">' + h.map(([n, w]) => '<i class="tab">' + n + '</i><span>' + esc(w) + '</span>').join("") + '</span>'
        : '<span class="hold none">none ' + RANGE_WORD[view.range] + '</span>') + '</p>'; }).join("") +
      (t.length ? '<p class="try">Let in by try, ' + RANGE_WORD.a + ': ' + t.map(x => '<b>' + (x[0] === 4 ? "4th and later" : nth(x[0])) + ' ' + pct(x[2], x[1]) + '%</b>').join(" \u00b7 ") +
        '. An address gets three tries; the refused wait behind the fresh.</p>' : '') + '</div>' : '');
  if (html === whatWas.h) return;
  el("what").innerHTML = html;
  whatWas.h = html;
}

/* --------------------------------------------------------- the batches */
/* A batch's name as people say it. The farm's names are typed by hand
   with the day glued on - "LEO 1OCT", "SONJIT 2Oct", "sonjit 23sep" - and
   the O of OCT beside a 1 or a 2 reads as a nought ("10CT"). The page says
   the seller in capitals and the day apart: "LEO · 1 Oct", and names with
   a day typed apart only in capitals or a space are one batch. A name
   with no day stays as typed: "SONJIT" on 2 Oct and "Sonjit" on 16 Sep
   are two orders. */
const MON = MONTHS.map(m => m.toUpperCase());
const NOSELL = "No seller";
const famOf = sell => {
  const s = String(sell || "").trim().replace(/\s+/g, " ");
  if (!s) return NOSELL;
  const m = /^(.*?)\s*\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)$/i.exec(s);
  return m ? (m[1] ? m[1].trim().toUpperCase() + " · " : "") + (+m[2]) + " " + MONTHS[MON.indexOf(m[3].toUpperCase())] : s;
};
/* A row's line names its batch, and the day it was bought when the name
   does not already say it. */
const bought = e => { const d = day(e.bought || e.added), f = famOf(e.sell); return !d || f.endsWith(" · " + d) ? "" : " · bought " + esc(d); };
/* The names a batch was typed under: its rows in the archive are found
   by these, since the farm keeps each as it was typed. */
const famSellers = key => Object.keys(DATA.batch || {}).filter(k => famOf(k) === key);
const noFam = () => ({n: 0, arch: 0, g: {t: {}, d: {}, a: {}}, v: {t: {}, d: {}, a: {}}, vp: {t: {}, d: {}, a: {}}, first: "", last: ""});
let famSeq = null, famsDrawn = false;
function famList() {
  const live = {}, stats = {};
  GM.forEach(e => { const id = famOf(e.sell); (live[id] || (live[id] = [])).push(e); });
  // The farm's counts, gathered under the names they show.
  Object.entries(DATA.batch).forEach(([key, b]) => {
    const id = famOf(key), m = stats[id] || (stats[id] = noFam());
    m.n += b.n || 0;
    m.arch += b.arch || 0;
    ["g", "v"].forEach(side => RANGES.forEach(([r]) => Object.entries(((b[side] || {})[r]) || {}).forEach(([w, c]) => { m[side][r][w] = (m[side][r][w] || 0) + c; })));
    // Its presses again, by the product each was on.
    const vp = (DATA.VPB || {})[key] || {};
    RANGES.forEach(([r]) => Object.entries(vp[r] || {}).forEach(([l, bs]) => {
      const o = m.vp[r][l] || (m.vp[r][l] = {});
      Object.entries(bs).forEach(([w, c]) => { o[w] = (o[w] || 0) + c; });
    }));
    if (b.first && (!m.first || b.first < m.first)) m.first = b.first;
    if (b.last && b.last > m.last) m.last = b.last;
  });
  const keys = new Set([...Object.keys(stats), ...Object.keys(live)]);
  const out = [...keys].map(key => {
    const b = stats[key] || noFam();
    const ex = live[key] || [];
    const g = gfold(b.g[view.range]), v = vfold(b.v[view.range]);
    const vp = Object.fromEntries(PK.map(l => [l, vfold(b.vp[view.range][l])]));
    // Kept for a product when every Gmail of it that can still be used is.
    const fl = [...new Set(ex.filter(canSwitch).map(e => e.for || ""))];
    const f = {key, name: key || "No seller", n: Math.max(b.n || 0, ex.length), arch: b.arch || 0, ex, g, v, gn: sum(g), vn: sum(v), vp,
      free: ex.filter(e => stateOf(e) === "free").length, first: b.first || "", last: b.last || "",
      on: ex.some(inPlay), switchable: ex.some(canSwitch), forL: fl.length === 1 ? fl[0] : ""};
    f.when = span(f.first, f.last);
    f.adv = advice(f);
    return f;
  }).filter(f => f.n >= 4 || f.ex.length);
  out.sort((a, b) => b.switchable - a.switchable || (b.last || "").localeCompare(a.last || "") || b.n - a.n)
    .sort((a, b) => (famSeq ? (famSeq.has(a.key) ? famSeq.get(a.key) : -1) - (famSeq.has(b.key) ? famSeq.get(b.key) : -1) : 0));
  if (!famSeq) famSeq = new Map(out.map((x, i) => [x.key, i]));
  return out;
}
/* The word on a batch, in the proxies' terms: Done 65%+ keeps it, Done
   under 40% or OR 40%+ stops it - and so does Google letting under 40% in.
   Under 8 pressed phones nothing is said. */
function advice(f) {
  if (f.vn < 8) return ["few", "too few presses"];
  const d = f.v[0] / f.vn, o = f.v[2] / f.vn, inr = f.gn >= 20 ? f.g[0] / f.gn : null;
  if (d < 0.4 || o >= 0.4 || (inr != null && inr < 0.4)) return ["stop", "Stop"];
  if (d >= 0.65) return ["keep", "Keep"];
  return ["watch", "Watch"];
}
/* A card a batch, read the same way every time - nothing on it moves when
   a key is pointed at. Its name and the word on it; Google's answers and
   the operators' presses as two rings, each with its first share inside
   (let in, Done) and its worst other share beside; and where its Gmails
   are now. Its menu acts on the whole batch at once. */
const worstOf = (keys, v) => keys.map((k, i) => [k, v[i]]).slice(1).filter(x => x[1]).sort((a, b) => b[1] - a[1])[0];
const worstHtml = (w, n, low) => w ? '<i class="mk ' + w[0] + '"></i>' + pct(w[1], n) + '% ' + esc(low[w[0]]) : '';
function cardDial(side, keys, low, v, n, unit, vp, fk) {
  const worst = worstOf(keys, v);
  const first = n ? pct(v[0], n) + '%' : '—', of = n ? "of " + plural(n, unit) : "no " + unit + "s " + RANGE_WORD[view.range];
  // Beside the word Done, a tile for each product the phones were on, with its own Done share.
  const pl = vp ? PK.filter(l => sum(vp[l])) : [];
  // What the dial says at rest is kept on it, to come back to when the pointer leaves.
  return '<span class="d2" data-side="' + side + '" data-n="' + n + '" data-unit="' + unit + '" data-b="' + first + '" data-w="' + esc(low[keys[0]]) + '" data-of="' + esc(of) + '"' +
    (pl.length ? " data-v='" + JSON.stringify(v) + "' data-vp='" + JSON.stringify(vp) + "'" : '') + '>' +
    '<span class="rg">' + ring(keys.map((k, i) => [v[i], k]), true) + '<b class="tab">' + first + '</b></span>' +
    '<span class="tx"><span class="t1"><b>' + low[keys[0]] + '</b>' +
    (pl.length ? '<span class="pl">' + pl.map(l => { const m = sum(vp[l]);
      return '<span class="pt' + (m < 5 ? ' few' : '') + '" data-l="' + l + '" data-fk="' + esc(fk) + '">' + tile(l, "xs") + '<b class="tab">' + pct(vp[l][0], m) + '%</b></span>'; }).join("") + '</span>' : '') + '</span>' +
    '<small class="wl"' + (worst ? ' data-has="1">' + worstHtml(worst, n, low) : ' hidden>') + '</small>' +
    '<small class="of tab">' + of + '</small></span></span>';
}
/* A product's tile beside a card's Done: pointing at it puts only that
   product's presses on the ring - its Done share inside, its worst other
   share and its phones beside - and leaving puts the whole batch back. */
function dialProd(d, l) {
  if (!d || !d.dataset.vp || (d.dataset.l || "") === l) return;
  const v = l ? JSON.parse(d.dataset.vp)[l] : JSON.parse(d.dataset.v), n = sum(v), w = worstOf(VK, v);
  if (!v || !n) return;
  d.dataset.l = l;
  d.dataset.n = n;
  d.dataset.unit = l ? PW[l] + " phone" : "phone";
  d.dataset.b = pct(v[0], n) + "%";
  d.dataset.of = "of " + plural(n, d.dataset.unit);
  d.querySelector(".rg svg").outerHTML = ring(VK.map((k, i) => [v[i], k]), true);
  const wl = d.querySelector(".tx .wl");
  wl.dataset.has = w ? "1" : "";
  wl.innerHTML = worstHtml(w, n, VW);
  d.classList.toggle("prod", !!l);
  d.querySelectorAll(".pt").forEach(p => p.classList.toggle("on", p.dataset.l === l));
  dialShow(d, "");
}
/* A card's ring answers the pointer on its own: the part pointed at lights
   and the rest step back, and the middle and the words beside say that
   part - its share and how many - until the pointer leaves. Nothing
   outside the card moves. */
function dialShow(d, k) {
  if (!d) return;
  const low = d.dataset.side === "g" ? GLOW : VW, n = +d.dataset.n, tap = k && d.querySelector('.tap[data-k="' + k + '"]');
  const b = d.querySelector(".rg b"), w = d.querySelector(".t1>b"), wl = d.querySelector(".tx .wl"), of = d.querySelector(".tx .of");
  k = tap ? k : "";
  d.classList.toggle("hot", !!k);
  d.querySelectorAll(".arc").forEach(a => a.classList.toggle("on", !!k && a.dataset.k === k));
  if (!k) { b.textContent = d.dataset.b; b.className = "tab"; w.textContent = d.dataset.w; w.className = ""; wl.hidden = !wl.dataset.has; of.textContent = d.dataset.of; return; }
  const c = +tap.dataset.c;
  b.textContent = pct(c, n) + "%"; b.className = "tab " + k; w.textContent = low[k]; w.className = k; wl.hidden = true;
  of.textContent = c + " of " + plural(n, d.dataset.unit);
}
const WHERE = [["free", "free", "var(--green)"], ["waiting", "waiting", "var(--amber)"], ["phone", "on phones", "var(--blue)"],
  ["out", "out of the queue", "var(--red)"], ["spent", "spent", "#3a4559"], ["arch", "archived", "#262f42"]];
function whereNow(f) {
  const c = {free: 0, waiting: 0, phone: 0, out: 0, spent: 0, arch: f.arch || 0};
  f.ex.forEach(e => { const s = stateOf(e); c[s === "stopped" || s === "aside" ? "out" : s]++; });
  // Nothing of it left in the pool: the bar says where every one went.
  if (!f.ex.length && c.arch) return '<span class="now gone"><span class="nbar" aria-hidden="true"><i style="flex:1;--kc:var(--edge)"></i></span>' +
    '<span class="nkey"><span><i style="--kc:var(--edge)"></i><b class="tab">All ' + c.arch + '</b> in the archive \u00b7 none left in the pool</span></span></span>';
  const parts = WHERE.filter(([k]) => c[k]);
  return '<span class="now"><span class="nbar" aria-hidden="true">' + parts.map(([k, , col]) => '<i style="flex:' + c[k] + ';--kc:' + col + '"></i>').join("") + '</span>' +
    '<span class="nkey">' + parts.map(([k, w, col]) => '<span><i style="--kc:' + col + '"></i><b class="tab">' + c[k] + '</b> ' + w + '</span>').join("") + '</span></span>';
}
/* What the batch menu can do to every Gmail of it at once; each item says
   how many it touches, and only what would change something is offered. */
function famMenu(key) {
  const f = famList().find(x => x.key === key);
  if (!f) return "";
  const ex = f.ex, out = ex.filter(e => isOut(e) && !unreadable(e)), play = ex.filter(inPlay), unused = ex.filter(canSwitch);
  const spent = ex.filter(e => stateOf(e) === "spent"), arch = ex.filter(canRemove), held = ex.length - arch.length;
  const b = (how, icon, words, bad) => '<button type="button"' + (bad ? ' class="bad"' : '') + ' data-fdo="' + how + '" data-fk="' + esc(key) + '">' + SVG(icon, 2) + words + '</button>';
  // The product its unused Gmails are for - one of the three, the current one marked.
  const kept = !unused.length ? "" : PK.concat([""]).map(l => '<button type="button" role="menuitemradio" aria-checked="' + (f.forL === l) + '"' + (f.forL === l ? ' class="on"' : '') +
    ' data-fdo="for:' + l + '" data-fk="' + esc(key) + '">' + tile(l, "xs") + (l ? "Keep for " + PW[l] : "For any product") + '</button>').join("");
  return [ex.length ? b("show", ICON.list, "Show its " + plural(ex.length, "Gmail")) + b("copy", ICON.copy, "Copy " + plural(ex.length, "address", "addresses")) : "", kept,
    (out.length ? b("free", ICON.back, "Put the " + out.length + " out of the queue back in") + b("mend", ICON.fix, "Mark the " + out.length + " out of the queue as fixed") : "") +
    (play.length ? b("aside", ICON.pause, "Set the " + play.length + " in the queue aside") : "") +
    // Into the archive: its spent ones alone, or the whole batch - a Gmail
    // on a phone always stays.
    (spent.length && spent.length < arch.length ? b("remove-spent", ICON.box, "Archive its " + plural(spent.length, "spent Gmail"), true) : "") +
    (arch.length ? b("remove-all", ICON.box, held ? "Archive the batch \u00b7 " + plural(arch.length, "Gmail") + ", the " + held + " on a phone stay"
      : "Archive the whole batch \u00b7 " + plural(arch.length, "Gmail"), true) : "")].filter(Boolean).join('<span class="sepm"></span>');
}
function renderFams() {
  // The batches with Gmails in the pool first; those whose every Gmail is
  // in the archive after them, under their own heading, when asked for.
  const every = famList(), here = every.filter(f => f.ex.length), gone = every.filter(f => !f.ex.length);
  const all = view.old ? here.concat(gone) : here;
  const still = famsDrawn ? " still" : "";
  famsDrawn = true;
  el("btsum").textContent = here.filter(f => f.on).length + " of " + here.length + " in the queue";
  el("bts").innerHTML = all.map((f, i) => {
    // The days its Gmails came in, unless the name already says that day.
    const k = esc(f.key), when = f.when && f.name !== f.when && !f.name.endsWith(" · " + f.when) ? " · " + esc(f.when) : "";
    const open = menu === "fam:" + f.key, word = f.adv[1][0].toUpperCase() + f.adv[1].slice(1);
    // A batch kept for one product wears that product's tile and its light.
    const kf = f.forL, out = !f.ex.length;
    return (out && i === here.length ? '<div class="btgap"><b>In the archive</b><small>' + plural(gone.length, "batch", "batches") +
        ' whose Gmails were all spent or archived \u2014 none left in the pool.</small></div>' : '') +
      '<div class="bt' + (kf ? " kept k-" + kf : "") + (out ? " gone" : "") + (view.fam === f.key ? " on" : "") + (f.switchable && !f.on ? " off" : "") + (flashFam === f.key ? " flash" : "") + still + '" style="--i:' + i + '">' +
      (out ? '<button class="hit" type="button" data-arcf="' + k + '" aria-label="Show the Gmails of ' + esc(f.name) + ' in the archive"></button>'
        : '<button class="hit" type="button" data-fam="' + k + '" aria-pressed="' + (view.fam === f.key) + '" aria-label="Show only the Gmails of ' + esc(f.name) + '"></button>') +
      '<span class="hd">' + (kf ? tile(kf, "m") : '') + '<span class="nm"><b>' + esc(f.name) + '</b><small><span class="adv ac-' + f.adv[0] + '">' + word + '</span>' +
      (kf ? '<span class="kept-w">kept for ' + PW[kf] + '</span>' : '') + f.n + ' bought' + when + '</small></span>' +
      (f.switchable ? '<button class="sw' + (litFam === f.key ? ' lit' : '') + '" type="button" role="switch" aria-checked="' + f.on + '" data-bsw="' + k + '" aria-label="' + esc(f.name) + ' in the queue" title="' + (f.on ? "In the queue: turn off to set its Gmails aside" : "Out of the queue: turn on to put its Gmails back") + '"><i></i></button>' : '') +
      (f.ex.length ? '<button class="xs more' + (open ? ' on' : '') + '" type="button" data-menu="fam:' + k + '" aria-haspopup="menu" aria-expanded="' + open + '" aria-label="More for ' + esc(f.name) + '">' + SVG(ICON.more, 2) + '</button>'
        : '<button class="arcb" type="button" data-arcf="' + k + '" title="Every Gmail of it is in the archive: open them there">' + SVG(ICON.box, 1.9) + 'In the archive</button>') + '</span>' +
      '<span class="two">' + cardDial("g", GK, GLOW, f.g, f.gn, "sign-in") + cardDial("v", VK, VW, f.v, f.vn, "phone", f.vp, f.key) + '</span>' + whereNow(f) + '</div>';
  }).join("");
  const older = every.length - here.length;
  el("btmore").innerHTML = older ? '<button class="link btmore" type="button" data-old="1">' + (view.old ? "Hide the " + plural(older, "batch", "batches") + " in the archive"
    : "Show the " + plural(older, "batch", "batches") + " already in the archive") + '</button>' : '';
  flashFam = "";
}

/* ------------------------------------------------------------- the list */
/* How long a Gmail has been on a phone. Signed in and waiting there now:
   from its sign-in to the page's moment. Spent: from its sign-in to the
   operator's press, the wait it had. Claimed by a build that has not
   signed it in yet: no clock to read. */
function phoneSpan(e) {
  const s = stateOf(e), ok = (e.t || []).filter(u => u[1]).pop(), p = lastPress(e);
  if (s === "phone") return e.st === "in_use" || !ok ? {now: true, signing: true} : {now: true, mins: Math.max(0, (NOW - tOf(ok[0])) / 6e4), from: ok[0]};
  if (s === "spent" && ok && p && tOf(p[0]) >= tOf(ok[0])) return {now: false, mins: (tOf(p[0]) - tOf(ok[0])) / 6e4, press: VOF[p[1]]};
  return null;
}
/* A stretch of minutes as people say it: 38 min, 3 h 10 min, 2 d 4 h. */
const dur = m => m < 1 ? "under a minute" : m < 60 ? Math.floor(m) + " min"
  : m < 1440 ? Math.floor(m / 60) + " h" + (Math.floor(m % 60) ? " " + Math.floor(m % 60) + " min" : "")
  : Math.floor(m / 1440) + " d" + (Math.floor(m % 1440 / 60) ? " " + Math.floor(m % 1440 / 60) + " h" : "");
function phoneCell(e) {
  const w = phoneSpan(e);
  if (!w) return "";
  if (w.signing) return '<span class="ph sig"><b>signing in</b><small>a build has it now</small></span>';
  return '<span class="ph ' + (w.now ? "now" : "past") + '"><b class="tab">' + dur(w.mins) + '</b><small>' +
    (w.now ? "waiting since " + when(w.from) : "until " + VW[w.press]) + '</small></span>';
}
/* The orders the list can take, each said in words both ways; a tie goes
   to the newer address. The menu holds them all; the column heads keep
   their own. */
const SORTS = {
  state: {w: ["Free first", "Spent first"], dir: 1, val: e => ORDER[stateOf(e)] * 100 - triesNow(e).length},
  // Longest first: the phones waiting now, then those signing in, then the waits the spent ones had.
  phone: {w: ["Longest on a phone first", "Shortest on a phone first"], dir: -1, val: e => { const w = phoneSpan(e); return !w ? -1 : w.signing ? 1e6 : w.now ? 2e6 + w.mins : w.mins; }},
  added: {w: ["Newest first", "Oldest first"], dir: -1, val: e => tOf(e.added) || 0},
  tries: {w: ["Most tries first", "Fewest tries first"], dir: -1, val: e => triesNow(e).length},
  rounds: {w: ["Most captcha rounds first", "Fewest captcha rounds first"], dir: -1, val: e => sum(triesNow(e).map(u => u[3] || 0))},
  google: {w: ["By Google’s answer", "By Google’s answer, reversed"], dir: 1, val: e => { const a = answerOf(e); return a ? GK.indexOf(a.k) : 9; }},
  press: {w: ["By the press", "By the press, reversed"], dir: 1, val: e => { const p = pressOf(e); return p ? VK.indexOf(p) : 9; }},
  product: {w: ["By product", "By product, reversed"], dir: 1, val: e => { const l = prodOf(e); return l ? PL.indexOf(l) : 9; }},
  name: {w: ["Name, A to Z", "Name, Z to A"], dir: 1},
};
const SORT_MENU = [["state", "phone", "added"], ["tries", "rounds"], ["google", "press", "product"], ["name"]];
const sortWord = (k, dir) => SORTS[k].w[dir === SORTS[k].dir ? 0 : 1];
/* Show only: what is worth a look, each a rule in its own words with how
   many Gmails of the list as filtered it holds. One at a time; the rule of
   the one chosen is spelled out under the row. */
const googleRefused = u => !u[1] && gkey(u[2], 0) !== "gnone";
const FLAGS = [
  {k: "fresh", w: "Never tried", c: "var(--green)", test: e => stateOf(e) === "free" && !e.back && !triesNow(e).length,
   why: "Free and never tried. The next build takes these, and the ones marked fixed, before any that were refused."},
  {k: "again", w: "Refused before", c: "var(--amber)", test: e => inPlay(e) && triesNow(e).some(googleRefused),
   why: "In the queue after Google refused it at least once; builds reach these after the never-tried ones."},
  {k: "last", w: "Last try left", c: "var(--red)", test: e => inPlay(e) && (e.tries || 0) === LADDER - 1,
   why: "Refused twice; one more refusal takes it out of the queue."},
  {k: "long", w: "On a phone 1 h+", c: "var(--blue)", test: e => { const w = phoneSpan(e); return !!w && w.now && !w.signing && w.mins >= 60; },
   why: "Signed in on a phone an hour or more ago, and no operator has pressed anything on it yet."},
  {k: "nokey", w: "No second factor", c: "var(--g-bad)", test: e => !e.key && !e.rec && stateOf(e) !== "spent",
   why: "The row has neither an authenticator key nor a recovery address, so a second step from Google finds nothing to answer with."},
  {k: "today", w: "Added today", c: "var(--violet)", test: e => (e.added || "").startsWith(NOWS.slice(0, 10)),
   why: "Added to the pool today."},
  // Drawn only once a Gmail has been marked fixed.
  {k: "mended", w: "Marked fixed", c: "var(--fix)", test: e => !!e.back, only: true,
   why: "Refused by Google, then mended and marked fixed: its tries started again, and what it did before is in its story."},
];
const FLAG = Object.fromEntries(FLAGS.map(f => [f.k, f]));
/* `skip` leaves one filter out, so each control counts what pressing it
   would show. */
function passes(e, skip) {
  if (skip !== "scope" && view.scope && !SCOPE[view.scope](e)) return false;
  if (skip !== "flag" && view.flag && !FLAG[view.flag].test(e)) return false;
  if (view.gk && (answerOf(e) || {}).k !== view.gk) return false;
  if (view.vk && pressOf(e) !== view.vk) return false;
  if (view.prod && prodOf(e) !== view.prod) return false;
  if (view.fam && famOf(e.sell) !== view.fam) return false;
  if (view.q) {
    const a = answerOf(e), p = lastPress(e);
    const text = [e.a, e.sell, famOf(e.sell), e.serial, STATE[stateOf(e)], a ? GW[a.k] + " " + rawWord(a.raw) : "not tried", p ? VW[VOF[p[1]]] + " " + p[2] : "", prodOf(e) ? PW[prodOf(e)] : "", keptFor(e) ? "kept for " + PW[keptFor(e)] : "", e.back ? "marked fixed" : "", e.key ? "" : "no key"].join(" ").toLowerCase();
    if (!text.includes(view.q.toLowerCase())) return false;
  }
  return true;
}
const narrowed = () => !!view.scope || !!view.flag || !!view.gk || !!view.vk || !!view.prod || !!view.fam || !!view.q;
/* Over the list, as the Proxy pool has it: where the Gmails are, each
   place with how many; then Show only and, at its end, the order. */
function renderTools() {
  const cnt = (skip, f) => GM.filter(e => passes(e, skip) && f(e)).length;
  el("scopeseg").innerHTML = seg("scope", "Show", [["", "All", cnt("scope", () => true)]].concat(
    ["free", "waiting", "phone", "out", "spent"].map(k => [k, SCOPE_WORD[k], cnt("scope", SCOPE[k])])), view.scope);
  el("sortbox").innerHTML = '<button class="sortb' + (menu === "sort" ? " on" : "") + '" type="button" data-menu="sort" aria-haspopup="menu" aria-expanded="' + (menu === "sort") + '" aria-label="Sort: ' + sortWord(view.sort, view.dir) + '">' +
    '<small>Sort</small><b>' + sortWord(view.sort, view.dir) + '</b>' + SVG(ICON.chev, 2.2) + '</button>';
  // A rule with nothing in it is drawn faint.
  el("flags").innerHTML = '<span class="k">Show only</span>' + FLAGS.map(f => { const n = cnt("flag", f.test);
    if (f.only && !n && view.flag !== f.k && !GM.some(f.test)) return "";
    return '<button type="button" class="flag" style="--fc:' + f.c + '" data-flag="' + f.k + '" aria-pressed="' + (view.flag === f.k) + '"' + (n || view.flag === f.k ? '' : ' disabled') + '>' + f.w + ' <b class="tab">' + n + '</b></button>'; }).join("");
  const on = view.flag ? FLAG[view.flag] : null;
  el("flagwhy").innerHTML = on ? '<p class="flagwhy"><b>' + on.w + '</b> · ' + on.why + '</p>' : '';
}
function shown() {
  const rows = GM.filter(passes);
  const nm = (a, b) => a.a.localeCompare(b.a);
  const S = SORTS[view.sort] || SORTS.state;
  rows.sort((a, b) => {
    if (!S.val) return nm(a, b) * view.dir;
    const d = S.val(a) - S.val(b);
    return d * view.dir || (b.added || "").localeCompare(a.added || "") || nm(a, b);
  });
  return rows;
}
function renderChips() {
  const chip = (what, word, cls) => '<button class="chip" type="button" data-clear="' + what + '" aria-label="Stop showing only ' + esc(word) + '">' + (cls ? '<i class="' + cls + '"></i>' : '') + '<span>' + esc(word) + '</span>' + SVG(ICON.cross, 2.6) + '</button>';
  // Where it is and Show only say themselves over the list; these are the filters set from elsewhere on the page.
  el("chips").innerHTML = [view.gk && chip("gk", "Last answer: " + GW[view.gk], view.gk),
    view.vk && chip("vk", "Last press: " + VW[view.vk], view.vk),
    view.prod && '<button class="chip" type="button" data-clear="prod" aria-label="Stop showing only the Gmails used on ' + PW[view.prod] + '">' + tile(view.prod, "xs") + '<span>Used on ' + PW[view.prod] + '</span>' + SVG(ICON.cross, 2.6) + '</button>',
    view.fam && chip("fam", "Batch " + view.fam, "")].filter(Boolean).join("") +
    (narrowed() ? '<button class="link" type="button" data-clear="all">Clear</button>' : '');
}
/* A row says its story in short words: where the address is and since
   when, what Google answered and on which try, what the operator pressed
   and who. */
function psw(e) {
  if (!canSwitch(e) || unreadable(e)) return "";
  const on = inPlay(e);
  return '<button class="sw' + (litIds.has(e.id) ? ' lit' : '') + '" type="button" role="switch" aria-checked="' + on + '" data-psw="' + e.id + '" aria-label="' + esc(e.a) + ' in the queue" title="' +
    (on ? 'In the queue: turn off to set it aside' : 'Out of the queue: turn on to put it back') + '"><i></i></button>';
}
function stateWord(e) {
  const s = stateOf(e), t = triesNow(e), refusals = t.filter(u => !u[1]).length;
  const tail = s === "free" ? (t.length ? "after " + plural(refusals, "refusal") : e.back ? "marked fixed" : "never tried")
    : s === "waiting" ? "back at " + when(e.next)
    : s === "phone" ? ""
    : s === "stopped" ? (unreadable(e) ? "details unreadable" : "after " + plural(t.length || e.tries || 0, "try", "tries"))
    : s === "aside" ? "by hand"
    : e.used ? day(e.used) : "";
  const word = s === "phone" && e.serial ? "On " + e.serial : STATE[s];
  return '<span class="dw s-' + s + '">' + esc(word) + (tail ? ' <small>' + esc(tail) + '</small>' : '') + '</span>';
}
function stateCell(e) { return '<span class="stc">' + psw(e) + stateWord(e) + '</span>'; }
function googleCell(e) {
  const a = answerOf(e), t = triesNow(e), s = stateOf(e);
  if (!a) return '<span class="dw none">' + (e.back ? "not tried since the fix" : "not tried yet") + '</span>';
  const left = s === "free" || s === "waiting" ? Math.max(0, LADDER - t.length) : 0;
  const pips = t.length ? '<span class="pips" aria-hidden="true">' + t.slice(-5).map(u => '<i class="' + gkey(u[2], u[1]) + '"></i>').join("") +
    Array.from({length: left}, () => '<i class="left"></i>').join("") + '</span>' : '';
  const okAt = t.findIndex(u => u[1]), aw = a.t ? answerWords(a.t) : "";
  // Under the category: the try that let it in, the rounds a puzzle took,
  // where Google asked for the phone - or what it said, in its words.
  const tail = a.k === "gin" ? nth(okAt + 1) + " try" + (a.t && a.t[3] ? ", " + plural(a.t[3], "captcha round") : "")
    : a.k === "gcap" ? [a.t && a.t[3] ? plural(a.t[3], "round") : "", aw].filter(Boolean).join(", ")
    : a.k === "gtel" ? [aw, t.length > 1 ? plural(t.length, "try", "tries") : ""].filter(Boolean).join(", ")
    : aw || rawWord(a.raw);
  return '<span class="gcell">' + pips + '<span class="g2"><span class="dw ' + a.k + '">' + GW[a.k] + '</span>' + (tail ? '<small>' + esc(tail) + '</small>' : '') + '</span></span>';
}
/* Where a Gmail's life ends: the product's tile, what the operator pressed,
   and under it the product and who. A Gmail on a phone shows that phone's
   product, dimmed, until the press comes. */
function pressCell(e) {
  const p = lastPress(e), s = stateOf(e);
  if (!p) {
    if (s === "phone" && e.on) return '<span class="pr wait">' + tile(e.on, "s") + '<span class="p2"><span class="dw none">not yet</span><small>on a ' + PW[e.on] + ' phone</small></span></span>';
    return s === "spent" ? '<span class="dw none">no press</span>' : s === "phone" ? '<span class="dw none">not yet</span>' : '';
  }
  const k = VOF[p[1]], l = p[4] || "gpt";
  return '<span class="pr">' + tile(l, "s") + '<span class="p2"><span class="dw ' + k + '">' + VW[k] + '</span><small>' + PW[l] + (p[2] ? ' · ' + esc(p[2]) : '') + '</small></span></span>';
}
function doors(e) {
  const open = menu === e.id + ":more";
  return '<span class="doors"><button type="button" class="more' + (open ? " on" : "") + '" data-menu="' + e.id + ':more" aria-haspopup="menu" aria-expanded="' + open + '" aria-label="More for ' + esc(e.a) + '">' + SVG(ICON.more, 2) + '</button></span>';
}
/* The list draws its first hundred rows, and the next hundred each time
   the reader nears its end (or presses the line that says how many are
   left). A thousand rows of a dozen parts each made every redraw - and
   every frame of the rail folding - lay out forty thousand nodes. A new
   filter, search or sort starts again at the top; the pool's own changes
   keep as many as were drawn. What the list's tools do - select every
   row, a batch's menu, the counts - is done on all the rows that match,
   drawn or not. */
const PAGE_ROWS = 100;
let rowLimit = PAGE_ROWS, rowView = "", moreSeen = null;
/* The ids drawn now, for the selection bar's "further down". */
let drawnIds = new Set();
function renderRows() {
  const rows = shown();
  const sig = JSON.stringify([view.range, view.scope, view.flag, view.fam, view.q, view.sort, view.dir, view.gk, view.vk, view.prod]);
  if (sig !== rowView) { rowView = sig; rowLimit = PAGE_ROWS; }
  const drawn = rows.slice(0, rowLimit), left = rows.length - drawn.length;
  drawnIds = new Set(drawn.map(e => e.id));
  el("rows").innerHTML = rows.length ? drawn.map(e =>
    '<tr data-id="' + e.id + '" class="' + (ticked.has(e.id) ? "ticked " : "") + (flashIds.has(e.id) ? "flash" : "") + '">' +
    '<td class="tk"><button class="box' + (ticked.has(e.id) ? " on" : "") + '" type="button" role="checkbox" aria-checked="' + ticked.has(e.id) + '" data-tick="' + e.id + '" aria-label="Select ' + esc(e.a) + '">' + SVG(ICON.tick, 3.4) + '</button></td>' +
    '<td><span class="g1"><span class="addr" data-open="' + e.id + '" role="button" tabindex="0" title="Open its story">' + esc(e.a) + '</span>' +
      '<small>' + esc(famOf(e.sell) || "no seller") + bought(e) + (keptFor(e) ? ' \u00b7 <b class="kept-w k-' + keptFor(e) + '">kept for ' + PW[keptFor(e)] + '</b>' : '') +
      (e.key ? '' : e.rec ? ' \u00b7 recovery address only' : ' \u00b7 <em>no second factor</em>') + '</small></span></td>' +
    '<td>' + stateCell(e) + '</td><td>' + phoneCell(e) + '</td><td>' + googleCell(e) + '</td><td>' + pressCell(e) + '</td><td>' + doors(e) + '</td></tr>').join("") +
    (left ? '<tr class="more-row"><td colspan="7"><button class="link" type="button" data-more="1">Show ' + Math.min(left, PAGE_ROWS) + ' more</button><small>' +
      left + ' of the ' + rows.length + ' not drawn yet \u00b7 they come as you scroll</small></td></tr>' : '')
    : '<tr class="none-row"><td colspan="7">' + SVG(ICON.mail, 1.5) + '<b>No Gmail matches that.</b>' + (narrowed() ? '<button class="link" type="button" data-clear="all">Clear the filters</button>' : '') + '</td></tr>';
  flashIds.clear();
  el("count").textContent = narrowed() ? rows.length + " of " + GM.length : String(GM.length);
  el("shown").innerHTML = '<b class="tab">' + rows.length + '</b> of <b class="tab">' + GM.length + '</b> Gmails shown';
  el("archn").textContent = archived;
  const every = rows.length > 0 && rows.every(e => ticked.has(e.id)), some = rows.some(e => ticked.has(e.id));
  el("all").classList.toggle("on", every);
  el("all").setAttribute("aria-checked", every ? "true" : some ? "mixed" : "false");
  // The next rows come before the reader reaches the end of these.
  const more = el("rows").querySelector(".more-row");
  if (moreSeen) moreSeen.disconnect();
  if (more && "IntersectionObserver" in window) {
    moreSeen = moreSeen || new IntersectionObserver(seen => {
      if (!seen.some(x => x.isIntersecting)) return;
      moreSeen.disconnect();
      rowLimit += PAGE_ROWS;
      renderRows();
      if (ticked.size) renderSel();
    }, {rootMargin: "0px 0px 900px 0px"});
    moreSeen.observe(more);
  }
  document.querySelectorAll("thead th.sortable").forEach(th => {
    const on = th.dataset.sort === view.sort;
    th.classList.toggle("up", on && view.dir === 1);
    th.classList.toggle("down", on && view.dir === -1);
    th.setAttribute("aria-sort", on ? (view.dir === 1 ? "ascending" : "descending") : "none");
  });
}
/* The legend is the control: the pressed keys, and the key the pointer is
   on, set which segments step back and the large number of each lane. */
function setFocus(side, key) {
  const m = document.querySelector("main");
  const k = key || (side === "g" ? view.gk : view.vk);
  if (side === "g") { m.dataset.g = k || "gin"; if (k) m.dataset.gf = k; else delete m.dataset.gf; }
  else { m.dataset.v = k || "kd"; if (k) m.dataset.vf = k; else delete m.dataset.vf; }
}
function renderPop() {
  const p = el("pop"), d = el("layer").querySelector(".draw"), dp = d && d.querySelector(".dpop");
  if (!menu) { p.innerHTML = ""; if (dp) dp.remove(); return; }
  const at = '[data-menu="' + CSS.escape(menu) + '"]', btn = el("layer").querySelector(at) || document.querySelector(at);
  if (!btn) { menu = ""; p.innerHTML = ""; return; }
  const inDraw = !!btn.closest(".draw");
  // A batch's menu, or a Gmail's.
  let html = "";
  if (menu === "sort") html = SORT_MENU.map(g => g.map(k => '<button type="button" role="menuitemradio" aria-checked="' + (view.sort === k) + '" class="' + (view.sort === k ? "on" : "") + '" data-sortk="' + k + '">' +
    sortWord(k, SORTS[k].dir) + '</button>').join("")).join('<span class="sepm"></span>');
  else if (menu.startsWith("fam:")) html = famMenu(menu.slice(4));
  else {
    const id = +menu.split(":")[0], e = GM.find(x => x.id === id);
    // In the drawer its footer holds the edit and the fix; the menu keeps the rest.
    if (e) html = (inDraw ? '' : '<button type="button" data-do="story" data-id="' + id + '">' + SVG(ICON.phone, 2) + 'Open its story</button>' +
        (canEdit(e) ? '<button type="button" data-do="edit" data-id="' + id + '">' + SVG(ICON.edit, 2) + 'Edit details</button>' : '') +
        (canMend(e) ? '<button type="button" data-do="mend" data-id="' + id + '">' + SVG(ICON.fix, 2) + 'Mark as fixed</button>' : '')) +
      '<button type="button" data-do="copy" data-id="' + id + '">' + SVG(ICON.copy, 1.9) + 'Copy the address</button>' +
      (canRemove(e) ? '<button type="button" class="bad" data-do="remove" data-id="' + id + '">' + SVG(ICON.box, 1.9) + 'Archive</button>' : '');
  }
  if (!html) { menu = ""; p.innerHTML = ""; btn.classList.remove("on"); btn.setAttribute("aria-expanded", "false"); return; }
  let host = p;
  if (inDraw) { p.innerHTML = ""; host = dp || d.appendChild(Object.assign(document.createElement("div"), {className: "dpop"})); }
  else if (dp) dp.remove();
  host.innerHTML = '<div class="pop" role="menu" aria-label="' + (menu === "sort" ? "Sort" : menu.startsWith("fam:") ? "Batch actions" : "More") + '">' + html + '</div>';
  host.querySelectorAll(".pop>button").forEach(b => { if (!b.getAttribute("role")) b.setAttribute("role", "menuitem"); });
  hideNote();
  const r = btn.getBoundingClientRect(), m = host.firstElementChild, mw = m.offsetWidth, mh = m.offsetHeight;
  let left = Math.min(r.left, window.innerWidth - mw - 8), top = r.bottom + 6;
  if (top + mh > window.innerHeight - 8) top = r.top - mh - 6;
  m.style.left = Math.max(8, left) + "px"; m.style.top = Math.max(8, top) + "px";
}
function renderSel() {
  const host = el("sel");
  if (!ticked.size) { host.innerHTML = ""; document.querySelector("main").style.paddingBottom = ""; return; }
  const sel = GM.filter(e => ticked.has(e.id)), can = sel.filter(canSwitch), hid = sel.filter(e => !passes(e)).length;
  const rem = sel.filter(canRemove);
  // The switch speaks for the ticked Gmails it can move.
  const swc = can.filter(e => !unreadable(e)), on = swc.filter(inPlay).length;
  const st = !swc.length ? "" : on === swc.length ? "true" : on ? "mixed" : "false";
  // Ticked by "every row" but not drawn yet: further down the list.
  const below = sel.filter(e => passes(e) && !drawnIds.has(e.id)).length;
  const inner = '<b>' + ticked.size + ' selected' + (hid ? '<small>' + hid + ' not shown</small>' : '') +
    (below ? '<small class="dn">' + below + ' further down the list</small>' : '') + '</b>' +
    (swc.length ? '<span class="sws"><button class="sw" type="button" role="checkbox" aria-checked="' + st + '" data-bulk="' + (st === "true" ? "aside" : "free") + '" data-keep="1" aria-label="The ticked Gmails in the queue"><i></i></button>' +
      '<span>In the queue<small class="tab">' + on + ' of ' + swc.length + '</small></span></span>' : '') +
    (sel.some(canMend) ? '<button class="act" type="button" data-bulk="mend">' + SVG(ICON.fix, 2) + 'Mark as fixed</button>' : '') +
    '<button class="act" type="button" data-bulk="copy">Copy addresses</button>' +
    (rem.length ? '<button class="act bad" type="button" data-bulk="remove">Archive' + (rem.length < sel.length ? " " + rem.length : "") + '</button>' : '') +
    '<button class="xs" type="button" data-bulk="clear" aria-label="Clear the selection">' + SVG(ICON.cross, 2.2) + '</button>';
  const bar = host.querySelector(".sel");
  if (bar) bar.innerHTML = inner; else host.innerHTML = '<div class="sel">' + inner + '</div>';
  document.querySelector("main").style.paddingBottom = host.firstElementChild.offsetHeight + 60 + "px";
}
function renderList() { renderTools(); renderChips(); renderRows(); renderSel(); }
function renderAll() { renderStock(); renderWhat(); renderFams(); renderList(); renderPop(); setFocus("g", ""); setFocus("v", ""); }

/* ----------------------------------------------------------------- acts */
/* A note at the foot of the page, its dot in the colour of what happened:
   green back in the pool, amber set aside, red archived, violet changed. A
   change that can be taken back says Undo - the last one, while its note
   stands, or Ctrl+Z. */
let undoFn = null;
let undoFrom = "";
const hideNote = () => { const n = el("said"); n.classList.remove("up"); n.inert = true; undoFn = null; };
function say(text, tone, undo) {
  const n = el("said"), life = undo ? 6500 : 3600;
  n.classList.remove("up"); void n.offsetWidth;
  n.innerHTML = '<span>' + esc(text) + '</span>' + (undo ? '<button type="button" class="undo">Undo</button>' : '');
  n.classList.toggle("ok", tone === true || tone === "ok");
  n.classList.toggle("can", !!undo);
  n.dataset.tone = typeof tone === "string" ? tone : "";
  n.style.setProperty("--life", life + "ms");
  undoFn = undo || null;
  undoFrom = undo ? focusKey(document.activeElement) : "";
  n.inert = false;
  clearTimeout(noteTimer); noteTimer = setTimeout(hideNote, life);
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
function copyText(text, what, ok) {
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
    .then(() => { say(what + " copied.", true); if (ok) ok(); })
    .catch(() => say("The clipboard is not open here \u2014 select it and copy it by hand.", "amber"));
}
/* What a press moved, in words, and the Undo that takes it back. The farm
   moves a Gmail only if it still stands where this page drew it, says why
   it left the others, and its Undo puts back every Gmail nothing has moved
   since (gmails_revert). */
const TONE = {free: "ok", mend: "ok", aside: "amber", remove: "red"};
/* Which Gmails a press can move, as this page reads them; the farm reads
   them again. */
const PICK = {free: e => isOut(e) && !unreadable(e), aside: inPlay, mend: canMend, remove: canRemove};
const pickFor = kind => kind.startsWith("for:") ? (e => canSwitch(e) && (e.for || "") !== kind.slice(4)) : PICK[kind] || (() => false);
/* Why the farm left a Gmail where it was, counted. */
const LEFTW = {phone: ["is on a phone now", "are on phones now"], spent: ["is spent", "are spent"],
  unreadable: ["has details the farm cannot read", "have details the farm cannot read"],
  gone: ["is no longer in the pool", "are no longer in the pool"], same: ["was already that way", "were already that way"]};
function leftWords(left) {
  const c = {};
  Object.values(left || {}).forEach(w => { c[w] = (c[w] || 0) + 1; });
  return Object.entries(c).map(([w, n]) => n + " " + (LEFTW[w] || [w, w])[n === 1 ? 0 : 1]).join(", ");
}
const cap1 = t => t ? t[0].toUpperCase() + t.slice(1) : t;
/* Undo: the farm takes the press back, Gmail by Gmail. */
function revert(reqs, addr) {
  const back = undoFrom;
  hideNote();
  press("/pools/gmail/revert", {reqs: reqs.join(",")}, a => {
    (a.back || []).forEach(id => { flashIds.add(id); SEC.delete(id); });
    renderAll();
    redrawDrawer();
    // Its details as they are again, if its drawer is open.
    const d = el("layer").querySelector(".draw:not([data-arch])");
    if (d && !SEC.has(+d.dataset.id)) askSecret(+d.dataset.id);
    const n = (a.back || []).length, m = (a.moved || []).length;
    say(!n ? "Nothing was taken back: " + (m === 1 ? "that Gmail has" : "those Gmails have") + " moved on since."
      : (addr && n === 1 ? "Undone: " + addr + " is as it was." : "Undone: the " + plural(n, "Gmail") + (n === 1 ? " is" : " are") + " as " + (n === 1 ? "it was." : "they were.")) +
        (m ? " " + m + " had moved on since and " + (m === 1 ? "stays" : "stay") + " as " + (m === 1 ? "it is." : "they are.") : ""), n ? "violet" : "amber");
    if (back && (!document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected || document.activeElement.closest("#said"))) refocus(back);
  }, a => say(a.note || a.said || "That change could not be taken back.", "amber"),
  // Taking back a Remove of hundreds puts each one back: it is given two minutes.
  120000);
}
function apply(kind, ids, who, endSelection) {
  const rows = GM.filter(e => ids.has(e.id));
  if (kind === "copy") {
    copyText(rows.map(e => e.a).join("\n"), plural(rows.length, "address", "addresses"));
    if (endSelection) { ids.forEach(id => ticked.delete(id)); menu = ""; renderAll(); }
    return;
  }
  const can = rows.filter(pickFor(kind)), only = can.length === 1 ? can[0].a : "";
  menu = "";
  if (!can.length) { renderAll(); redrawDrawer(); say("Nothing to change: already that way.", false); return; }
  renderPop();
  press("/pools/gmail/do", {what: kind, ids: can.map(e => e.id).join(",")}, a => {
    const moved = new Set(a.ids || []), n = moved.size;
    // The selection ends with the press that went through, not before it.
    if (endSelection) ids.forEach(id => ticked.delete(id));
    if (kind === "remove") moved.forEach(id => ticked.delete(id));
    else moved.forEach(id => flashIds.add(id));
    if (kind === "free" || kind === "mend") moved.forEach(id => { const e = GM.find(x => x.id === id); if (e && inPlay(e)) litIds.add(id); });
    if (view.fam && !GM.some(e => famOf(e.sell) === view.fam)) view.fam = "";
    renderAll();
    redrawDrawer();
    setTimeout(() => litIds.clear());
    litFam = "";
    const l = kind.startsWith("for:") ? kind.slice(4) : "";
    const words = kind === "free" ? "back in the queue" : kind === "aside" ? "set aside" : kind === "remove" ? "moved to the archive"
      : kind === "mend" ? "back in the pool as fixed; " + (n === 1 ? "its" : "their") + " tries start again"
      : l ? "kept for " + PW[l] + "; " + PW[PX[l]] + " builds pass " + (n === 1 ? "it" : "them") + " by" : "open to any product again";
    const one = who ? who + ": " + n : n === 1 && only ? only : plural(n, "Gmail");
    const rest = leftWords(a.left);
    say(n ? one + " " + words + "." + (rest ? " " + cap1(rest) + "." : "") : "Nothing changed: " + (rest || "already that way") + ".",
      n ? TONE[kind] || "violet" : false, n ? () => revert([a.req], n === 1 && only ? only : "") : null);
  }, a => { litFam = ""; say(a.note || a.said || "Nothing was changed.", "amber"); });
}
/* The batch switch: off, the ones in the queue are set aside; on, the ones
   set aside come back - or, if none were, the stopped are put back in.
   Phones and spent addresses are untouched. */
function setFam(key, on) {
  const f = famList().find(x => x.key === key);
  if (!f) return;
  const aside = f.ex.filter(e => e.st === "set_aside");
  const ids = new Set((on ? (aside.length ? aside : f.ex.filter(e => isOut(e) && !unreadable(e))) : f.ex.filter(inPlay)).map(e => e.id));
  flashFam = key;
  if (on) litFam = key;
  apply(on ? "free" : "aside", ids, f.name);
}

/* --------------------------------------------------------------- drawer */
function closeLayer() {
  const a = el("layer").querySelector(".draw");
  editing = null;
  // An archive search on its way is for a drawer that is gone.
  clearTimeout(archTimer);
  archAsk++;
  archFam = null;
  // A Gmail's details are read again each time its drawer opens.
  SEC.clear();
  el("layer").innerHTML = "";
  document.documentElement.classList.remove("locked");
  if (!a) return;
  const back = drawerFrom && drawerFrom.isConnected ? drawerFrom : document.querySelector('[data-open="' + a.dataset.id + '"]');
  drawerFrom = null;
  if (back) back.focus({preventScroll: true});
}
function openDrawer(id, from) {
  const e = GM.find(x => x.id === id);
  if (!e) return;
  if (!el("layer").querySelector(".draw")) { const a = from || document.activeElement; drawerFrom = a && a !== document.body ? a : null; }
  if (editing && editing.id !== id) editing = null;
  // An archive answer still on its way must not take this drawer's place.
  clearTimeout(archTimer);
  archAsk++;
  archFam = null;
  el("layer").innerHTML = '<div class="scrim"></div><aside class="draw opening" role="dialog" aria-modal="true" aria-label="' + esc(e.a) + '" data-id="' + e.id + '">' + drawerHtml(e) + '</aside>';
  document.documentElement.classList.add("locked");
  const a = el("layer").querySelector(".draw");
  askSecret(id);
  if (editing) edFocus(a); else a.querySelector(".xbtn").focus({preventScroll: true});
  setTimeout(() => a.classList.remove("opening"), 1200);
}
function redrawDrawer() {
  const a = el("layer").querySelector(".draw");
  if (!a || a.dataset.arch) return;
  const e = GM.find(x => x.id === +a.dataset.id);
  if (!e) { closeLayer(); return; }
  const top = a.querySelector(".body").scrollTop, had = a.contains(document.activeElement) && document.activeElement.dataset;
  const typed = editing && a.querySelector(".ed") ? ["ed-pass", "ed-key", "ed-rec", "ed-note"].map(id => [id, el(id).value]) : null;
  const inField = typed && a.contains(document.activeElement) && document.activeElement.closest(".ed") ? document.activeElement.id : "";
  // Gone to a phone meanwhile: its details stay as the build read them.
  if (editing && !canEdit(e)) editing = null;
  a.classList.remove("opening");
  a.innerHTML = drawerHtml(e);
  a.querySelector(".body").scrollTop = top;
  // A menu open from the drawer is drawn again over its new door.
  if (menu) renderPop();
  if (typed && editing) typed.forEach(([id, v]) => { const f = el(id); if (f) f.value = v; });
  if (inField && editing && el(inField)) { el(inField).focus({preventScroll: true}); return; }
  if (editing) { edFocus(a); return; }
  if (had) { const same = had.menu ? a.querySelector('[data-menu="' + CSS.escape(had.menu) + '"]') : had.psw ? a.querySelector("[data-psw]")
      : had.do ? a.querySelector('[data-do="' + CSS.escape(had.do) + '"]') : had.what ? a.querySelector('[data-what="' + CSS.escape(had.what) + '"]') : null;
    (same || a.querySelector(".xbtn")).focus({preventScroll: true}); }
}
/* An admin reads a Gmail's password, key and recovery address as they are,
   asked of the farm when its drawer opens and kept only while it is open -
   never in the page's state, which every poll carries. */
const SEC = new Map(), secWait = new Set();
const secOf = e => SEC.get(e.id) || null;
function askSecret(id) {
  if (SEC.has(id) || secWait.has(id)) return;
  secWait.add(id);
  get("/pools/gmail/secret?id=" + id).then(a => {
    secWait.delete(id);
    if (!a.ok) { if (a.go) { location.href = a.go; return; } say(a.note || "The farm did not send its details just now; open it again.", "amber"); return; }
    // Kept only while its drawer is open: closed meanwhile, it is not kept.
    const d = el("layer").querySelector(".draw");
    if (!d || +d.dataset.id !== id) return;
    SEC.set(id, {pw: a.pw || "", key: a.key || "", rec: a.rec || ""});
    redrawDrawer();
  });
}
/* A Gmail on a phone keeps the details the build read until the phone is
   done; the farm refuses an edit then, so the page does not offer one. */
const canEdit = e => stateOf(e) !== "phone";
const grouped = k => k.replace(/(.{4})/g, "$1 ").trim();
/* Its whole life, read top-down: where it is and why; Google's last answer
   and the operator's press as two tiles; then every event in order - each
   sign-in, each press, and what was done to it here - the time between
   them on the line; and its details, which open to be edited. */
let editing = null;
function drawerHtml(e) {
  const all = e.t || [], t = triesNow(e), p = e.p || [], s = stateOf(e), a = answerOf(e), lp = lastPress(e), wait = phoneSpan(e);
  let ix = 0;
  const at = () => ' style="--i:' + (ix++) + '"';
  const gap = (x, y) => { const h = (tOf(y) - tOf(x)) / 36e5; return isNaN(h) || h < 0.02 ? '' : '<div class="gap"' + at() + '><i></i><span>' + fmtH(h) + ' later</span></div>'; };
  const ev = (cls, w, word, small, right) => '<div class="ev ' + cls + '"' + at() + '><i class="dt"></i><span class="t">' + esc(w) + '</span><span class="w"><b>' + word + '</b>' + (small ? '<small>' + esc(small) + '</small>' : '') + '</span><span class="r">' + (right || '') + '</span></div>';
  // Every event in time order; a try is counted from the last time it was marked fixed before it.
  const backs = (e.log || []).filter(x => x[1] === "back").map(x => x[0]);
  const evs = all.map((u, i) => [u[0], () => {
    const [w, ok, why, rounds, secs, serial, host] = u, k = gkey(why, ok), aw = answerWords(u);
    const from = backs.filter(b => b < w).pop() || "", n = all.slice(0, i + 1).filter(v => v[0] > from).length;
    const where = [serial ? "phone " + serial : "", host ? "through " + host : "", secs ? Math.round(secs) + " s" : ""].filter(Boolean).join(" · ");
    return ev(k, w.slice(11), nth(n) + " try: <em>" + GW[k] + "</em>" + (aw ? " — " + esc(aw) : ""), where,
      rounds ? Array.from({length: Math.min(rounds, 10)}, () => '<i></i>').join("") + '<small>' + plural(rounds, "round") + '</small>' : '');
  }]).concat(p.map(x => [x[0], () => { const k = VOF[x[1]];
    return ev(k, x[0].slice(11), "<em>" + VW[k] + "</em> pressed on " + PW[x[4] || "gpt"], [x[2] ? "by " + x[2] : "", x[3] ? "phone " + x[3] : ""].filter(Boolean).join(" · ")); }]))
    .concat((e.log || []).map(x => [x[0], () => ev(x[1], x[0].slice(11), esc(x[2]), x[3])]));
  evs.sort((x, y) => x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0);
  let line = ev("born", day(e.added), famOf(e.sell) === NOSELL ? "Added with no seller" : "Added to " + esc(famOf(e.sell)), e.key ? "with an authenticator key" : e.rec ? "with a recovery address" : "with no second factor");
  let last = e.added;
  evs.forEach(([w, f]) => { line += gap(last, w); last = w; line += f(); });
  if (s === "waiting") line += ev("now", when(e.next), "Back in the queue", "");
  const caps = sum(all.map(u => u[3] || 0));
  const why2 = s === "stopped" ? (unreadable(e) ? "The farm cannot read its details, so nothing takes it: edit them to put it right, or archive it."
      : "Its tries are spent, so it waits for a person: turn it on to try again, or mark it fixed once it has been mended.")
    : s === "waiting" ? "Refused, and back in the queue at " + when(e.next) + "; the queue reaches it after the addresses never tried."
    : s === "aside" ? "Set aside by hand: nothing takes it until it is turned on."
    : s === "free" ? (t.length ? "Free again after " + plural(t.filter(u => !u[1]).length, "refusal") + "; the queue reaches it last."
      : e.back ? "Marked as fixed: its tries start again, and the next build can take it." : "Never tried: the next build can take it.")
    : s === "phone" ? (e.st === "in_use" ? "A build is signing it in" + (e.serial ? " on phone " + e.serial : "") + "; nothing else can take it."
      : "Signed in and carried by " + (e.serial ? "phone " + e.serial : "a phone") + ".")
    : (e.note || "Spent.");
  // The product: the one it went to, the one its phone is for, or what it is kept for.
  const prod = lp ? PW[lp[4] || "gpt"] : s === "phone" ? (e.on ? "on a " + PW[e.on] + " phone" : "—")
    : canSwitch(e) ? (e.for ? "kept for " + PW[e.for] : "any product") : "—";
  // A line: its label, its value - null while the farm is still sending it - and what Copy takes.
  const S = secOf(e);
  const secret = S ? [["Password", S.pw || "none", S.pw], ["Authenticator key", S.key ? grouped(S.key) : "none", S.key],
    ["Recovery address", S.rec || "none", S.rec]] : [["Password", null, ""], ["Authenticator key", null, ""], ["Recovery address", null, ""]];
  // Editing, the fields it can change leave the list for the form under it.
  const lines = [["Batch", famOf(e.sell) || "—"], ["Bought", day(e.bought || e.added) || "—"], ["Product", prod], ["Address", e.a, e.a]]
    .concat(editing ? [["Phone", e.serial || "—"]] : secret.concat([["Phone", e.serial || "—"], ["Note", e.note || "—"]]));
  const door = (k, icon, words, cls) => '<button type="button"' + (cls ? ' class="' + cls + '"' : '') + ' data-do="' + k + '" data-id="' + e.id + '">' + SVG(icon, 2) + words + '</button>';
  // The form waits for the farm's details: its doors are off until they come.
  const off = S ? '' : ' disabled';
  const foot = editing
    ? '<footer class="edf"><button type="button" class="ghost" data-ed="cancel">Cancel</button>' +
      (canMend(e) ? '<button type="button" class="ghost" data-ed="save"' + off + '>Save</button><button type="button" class="go" data-ed="fixed"' + off + '>' + SVG(ICON.fix, 2) + 'Save and mark fixed</button>'
        : '<button type="button" class="go" data-ed="save"' + off + '>Save</button>') + '</footer>'
    : '<footer><span class="doors">' + (canEdit(e) ? door("edit", ICON.edit, "Edit details") : '') + (canMend(e) ? door("mend", ICON.fix, "Mark as fixed", "pri") : '') + '</span>' + doors(e) + '</footer>';
  return '<header><span class="id"><b>' + esc(e.a) + '</b>' +
    '<span class="row">' + stateCell(e) + '</span>' +
    '<small class="why">' + esc(why2) + '</small></span>' +
    '<button class="xbtn" type="button" data-close="1" aria-label="Close">' + SVG(ICON.cross, 2.2) + '</button></header><div class="body">' +
    '<div class="kvs"><div class="kv ' + (a ? a.k : "") + '"><b>' + (a ? GW[a.k] : "Not tried yet") + '</b><span>Google&rsquo;s answer</span><small>' +
      (a ? esc((a.t && answerWords(a.t)) || (a.k === "gin" ? "let in" : rawWord(a.raw))) + (t.length ? " · " + plural(t.length, "try", "tries") : "")
        : e.back ? "not since the fix" : "no build has taken it") + '</small></div>' +
    '<div class="kv ' + (lp ? VOF[lp[1]] : "") + '">' + (lp ? tile(lp[4] || "gpt", "s") : s === "phone" && e.on ? tile(e.on, "s dim") : '') +
      '<b>' + (lp ? VW[VOF[lp[1]]] : "—") + '</b><span>Operator&rsquo;s press</span><small>' +
      (lp ? "on " + PW[lp[4] || "gpt"] + ", by " + esc(lp[2] || "someone") + (wait && !wait.now ? ", after " + dur(wait.mins) + " on the phone" : "")
      : wait && wait.now ? (wait.signing ? "a build is signing it in" : "waiting " + dur(wait.mins) + " on " + (e.on ? "a " + PW[e.on] + " phone" : "the phone"))
      : s === "spent" ? "the phone ended with no press" : "no phone yet") + '</small></div></div>' +
    '<h3>Its life' + (all.length ? '<small>' + plural(all.length, "sign-in") + (caps ? ', ' + plural(caps, "captcha round") : '') + '</small>' : '') + '</h3><div class="tl">' + line + '</div>' +
    '<h3>Details' + (editing ? '<small>editing</small>' : '') + '</h3><div class="lines">' +
    lines.map(([k, val, cp]) => '<div class="l1' + (cp ? " copy" : "") + '"' + (cp ? ' data-copy="' + esc(cp) + '" data-what="' + k + '" role="button" tabindex="0"' : '') + '>' +
      '<span class="k">' + k + '</span><span class="v">' + (val === null ? '<span class="ld"><span aria-hidden="true">···</span><span class="sr">being read</span></span>' : esc(val)) + '</span>' +
      (cp ? '<span class="cp">' + SVG(ICON.copy, 1.8) + '</span>' : '<span></span>') + '</div>').join("") + '</div>' +
    (editing ? (S ? edForm(e) : '<p class="edh">Reading its details from the farm\u2026</p>') : '') + '</div>' + foot + '<span class="sr" role="status" aria-live="polite"></span>';
}
/* Its details, open to edit, as they are: a field holds what the farm has,
   and what is in it when saved is what the farm keeps - an empty key or
   recovery address takes that one off. A Gmail Google refused can be saved
   and marked fixed in one go. */
function edForm(e) {
  const S = secOf(e) || {pw: "", key: "", rec: ""};
  const f = (id, label, val, ph) => '<div class="fld"><label class="k" for="' + id + '">' + label + '</label>' +
    '<input id="' + id + '" value="' + esc(val) + '" placeholder="' + ph + '" autocomplete="off" spellcheck="false"></div>';
  return '<form class="ed" id="ed" novalidate>' +
    (canMend(e) ? '<p class="edh">Mended by the seller or by hand? Correct what changed, then save and mark it fixed: it goes back to the pool and its tries start again.</p>' : '') +
    f("ed-pass", "Password", S.pw, "Its password") +
    f("ed-key", "Authenticator key", S.key ? grouped(S.key) : "", "None — paste one to add") +
    f("ed-rec", "Recovery address", S.rec, "None — type one to add") +
    '<div class="fld"><label class="k" for="ed-note">Note</label><textarea id="ed-note" rows="3" spellcheck="false">' + esc(e.note || "") + '</textarea></div>' +
    '<p class="err" id="ed-err" role="alert"></p></form>';
}
function edFocus(a) {
  const f = a.querySelector("#ed-pass");
  if (!f) return;
  f.focus({preventScroll: true});
  a.querySelector(".ed").scrollIntoView({block: "nearest", behavior: calm ? "auto" : "smooth"});
}
/* Save the details as they stand in the form - and, for a Gmail Google
   refused, mark it fixed: back to the pool, its tries started again. The
   farm judges them again and keeps them; Undo puts the old ones back. */
let saving = false;
function saveEdit(fixed) {
  const d = el("layer").querySelector(".draw"), e = d && GM.find(x => x.id === +d.dataset.id);
  if (!e || !editing || saving) return;
  const S = secOf(e), fp = el("ed-pass"), fk = el("ed-key"), fr = el("ed-rec"), err = el("ed-err");
  if (!S) return;
  const pass = fp.value.trim(), key = fk.value.replace(/[\s-]+/g, "").toUpperCase(), rec = fr.value.trim().toLowerCase(), note = el("ed-note").value.trim();
  const bad = !pass ? [fp, "A Gmail needs its password."]
    : key && !KEYISH.test(key) ? [fk, "A key is 16 or more letters A to Z and digits 2 to 7; spaces between its groups are fine."]
    : rec && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(rec) ? [fr, "A recovery address looks like name@outlook.com."]
    : rec && rec === e.a.toLowerCase() ? [fr, "The recovery address has to be another mailbox, not this Gmail."] : null;
  d.querySelectorAll(".ed [aria-invalid]").forEach(x => x.removeAttribute("aria-invalid"));
  if (bad) { hideNote(); bad[0].setAttribute("aria-invalid", "true"); err.textContent = bad[1]; bad[0].focus(); return; }
  const done = [];
  if (pass !== S.pw) done.push("password changed");
  if (key !== S.key) done.push(!key ? "key removed" : S.key ? "key changed" : "key added");
  if (rec !== S.rec) done.push(!rec ? "recovery address removed" : S.rec ? "recovery address changed" : "recovery address added");
  // The note is sent only when the person changed it: the farm's own note
  // is shown in this page's words, and sending those back would rewrite it.
  const noted = note !== (e.note || "").trim();
  if (noted) done.push("note edited");
  const said = done.join(", ").replace(/, ([^,]*)$/, " and $1");
  if (!said && !fixed) { editing = null; redrawDrawer(); say("Nothing changed.", false); return; }
  saving = true;
  d.querySelectorAll(".edf button").forEach(b => { b.disabled = true; });
  // Only what the person changed goes: a field left alone keeps what the
  // farm holds now, even if someone else changed it since this form opened.
  const sent = Object.assign({id: e.id, fixed: fixed ? "1" : "0"},
    pass !== S.pw ? {password: pass, has_password: "1"} : {}, key !== S.key ? {key, has_key: "1"} : {},
    rec !== S.rec ? {recovery: rec, has_rec: "1"} : {}, noted ? {note, has_note: "1"} : {});
  press("/pools/gmail/save", sent, a => {
    saving = false;
    // Read again from the farm, now that it holds what was saved.
    SEC.delete(e.id);
    editing = null;
    flashIds.add(e.id);
    if (a.mended) litIds.add(e.id);
    renderAll();
    redrawDrawer();
    const open = el("layer").querySelector(".draw:not([data-arch])");
    if (open && +open.dataset.id === e.id) askSecret(e.id);
    setTimeout(() => litIds.clear());
    const now = GM.find(x => x.id === e.id) || e;
    say(a.mended ? now.a + " is back in the pool as fixed" + (said ? ": " + said : "") + "."
      : (said ? now.a + ": " + said + "." : "Nothing changed.") + (a.unfixable ? " It was not refused since its last fix, so it is not marked fixed." : ""),
      a.mended ? "ok" : said ? "violet" : false, a.mended || said ? () => revert([a.req], now.a) : null);
  }, a => {
    saving = false;
    const f = el("layer").querySelector(".draw .ed");
    el("layer").querySelectorAll(".edf button").forEach(b => { b.disabled = false; });
    if (f) { el("ed-err").textContent = a.said || a.note || "The farm did not keep the details."; el("ed-pass").focus(); }
    else say(a.said || a.note || "The farm did not keep the details.", "amber");
  });
}

/* ---------------------------------------------------------- add Gmails */
/* reader:start - the dashboard's paste reader (web/paste.py, `accounts`),
   rule for rule, so a batch the dashboard reads this page reads the same
   way: lines it took were left out here, because this reader split every
   line on spaces, commas, colons, semicolons and bars at once - a
   password with any of them came apart - and took the field after the
   address for the password (2026-10-11). A test runs both readers over
   the same lines. A line splits on tabs (a sheet's copy), else commas,
   else spaces; the address is the piece with an @, the key the last piece
   shaped like base32 (or four or more spaced groups of four), a second
   address the recovery one, and the password the first piece left -
   wherever each sits. What this page adds: a line with no tab, comma or
   space splits on a seller's colons, bars or semicolons; a key may come
   in dashed groups; and a line may carry a key and a recovery address
   both, since a Gmail here keeps both. */
const KEYISH = /^[A-Z2-7]{16,}$/i, GROUP = /^[A-Z2-7]{4}$/i;
const isMail = p => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(p);
const B32 = /^[A-Za-z2-7 ]{16,}$/, DASHED = /^[A-Za-z2-7]{4}(?:-[A-Za-z2-7]{4}){3,}$/;
function splitLine(line) {
  const s = line.trim();
  const parts = s.includes("\t") ? s.split("\t") : s.includes(",") ? s.split(",") : /\s/.test(s) ? s.split(/\s+/) : s.split(/[:|;]/);
  return parts.map(p => p.trim()).filter(Boolean);
}
function regroup(tokens) {
  let best = 0, at = -1, run = 0;
  tokens.concat([""]).forEach((t, i) => {
    if (GROUP.test(t)) { run++; return; }
    if (run > best) { best = run; at = i - run; }
    run = 0;
  });
  if (best < 4) return ["", tokens.slice()];
  return [tokens.slice(at, at + best).join("").toUpperCase(), tokens.slice(0, at).concat(tokens.slice(at + best))];
}
function parseLine(line) {
  const parts = splitLine(line).map(p => DASHED.test(p) ? p.replace(/-/g, "") : p);
  const emails = parts.filter(isMail);
  let rest = parts.filter(p => !emails.includes(p));
  // The last base32-shaped piece: a password of sixteen plain letters is
  // base32-shaped too, and a seller's line puts the key after it.
  const keys = rest.filter(p => B32.test(p) && !/^\d+$/.test(p));
  let secret = keys.length ? keys[keys.length - 1] : "";
  rest = rest.filter(p => p !== secret);
  if (!secret) [secret, rest] = regroup(rest);
  let pass = rest[0] || "";
  // A password can be shaped like an address: with nothing else left for
  // it, the second address is the password.
  if (!pass && emails.length > 1) pass = emails.splice(1, 1)[0];
  if (!emails.length) return null;
  let key = secret.replace(/ /g, "").toUpperCase(), error = "";
  if (key && !pass) { pass = key; key = ""; error = "has a password the farm cannot tell from its 2fa key: put them in separate columns"; }
  const address = emails[0].toLowerCase(), rec = (emails[1] || "").toLowerCase();
  return {address, pass, key, rec, odd: rest.slice(1).concat(emails.slice(2)), error,
    second: key && rec ? "key and recovery" : key ? "key" : rec ? "recovery" : ""};
}
/* reader:end */
function draftRows() {
  const seen = new Map(), pool = new Map(GM.map(e => [e.a.toLowerCase(), e]));
  return el("paste").value.split(/\r?\n/).map((l, i) => ({raw: l.trim(), line: i + 1})).filter(r => r.raw).map(r => {
    const x = parseLine(r.raw), had = x && pool.get(x.address), back = had && canMend(had) ? had : null;
    const why = !x ? "has no address in it" : !x.pass ? "has no password" : x.error ? x.error
      : x.odd.length ? "has " + (x.odd.length === 1 ? "a piece" : x.odd.length + " pieces") + " it cannot place: " + x.odd.slice(0, 2).join(", ") + (x.odd.length > 2 ? ", \u2026" : "")
      : had && !back ? (stateOf(had) === "spent" ? "is spent" : stateOf(had) === "phone" ? "is on a phone"
        : unreadable(had) ? "has details the farm cannot read; edit it in its row" : "is in the queue already; edit it in its row")
      : seen.has(x.address) ? "repeats line " + seen.get(x.address) : "";
    if (x && !why) seen.set(x.address, r.line);
    return Object.assign(r, {x: why ? null : x, why, back: why ? null : back});
  });
}
/* What a line coming back fixed changes on its row, in words - and, short,
   for its line in the list. */
const backShort = x => "new " + ["password", x.key ? "key" : "", x.rec ? "recovery" : ""].filter(Boolean).join(", ").replace(/, ([^,]*)$/, " and $1");
/* Today as the farm counts it, for the batch a paste starts (`load`). */
const DAY = {iso: "", tag: "", word: ""};
const clean = s => String(s || "").replace(/[^A-Za-z0-9 ]/g, "").trim().replace(/\s+/g, " ");
/* The batch a paste goes into: the seller and the day it was bought. Its
   key keeps the farm's way of writing it ("LEO 2OCT"); the page shows its
   name ("LEO · 2 Oct"). A batch of that name already in the farm takes
   the paste in, under its own key. */
const batchOf = () => {
  const s = clean(el("f-seller").value).toUpperCase();
  if (!s) return {seller: "", key: "", name: "", had: 0, forL: ""};
  const name = famOf(s + " " + DAY.tag), key = [...Object.keys(DATA.batch), ...GM.map(e => e.sell)].find(k => k && famOf(k) === name) || s + " " + DAY.tag;
  // What the batch's unused Gmails are kept for now, if it has any.
  const fl = [...new Set(GM.filter(e => famOf(e.sell) === name && canSwitch(e)).map(e => e.for || ""))];
  return {seller: s, key, name, had: GM.filter(e => famOf(e.sell) === name).length, forL: fl.length === 1 ? fl[0] : ""};
};
let cmpOpen = false;
/* The product a paste is for: any, unless one is chosen. Joining a batch it
   starts at the batch's own, and a batch is for one thing - a different
   choice carries its unused Gmails with it. */
let forPick = "", forTouched = false;
function renderRead() {
  const rows = draftRows(), good = rows.filter(r => r.x), bad = rows.length - good.length, SHOW = 6, b = batchOf();
  const fresh = good.filter(r => !r.back), back = good.length - fresh.length;
  if (!forTouched) forPick = b.forL;
  el("forpick").querySelectorAll("button").forEach(x => { const on = x.dataset.pick === forPick; x.setAttribute("aria-checked", String(on)); x.tabIndex = on ? 0 : -1; });
  el("forwhy").textContent = forPick ? "Only " + PW[forPick] + " builds take them; " + PW[PX[forPick]] + " builds pass them by." : "A build for any product can take them.";
  const forSaid = b.had && forPick !== b.forL ? (forPick ? ", the batch kept for " + PW[forPick] + " from now" : ", the batch open to any product from now")
    : forPick ? ", kept for " + PW[forPick] : "";
  const box = el("cmp-read");
  box.hidden = !rows.length;
  // The new ones go to the batch named; a returning one keeps its own.
  const to = !fresh.length ? '' : b.seller ? (b.had ? 'joins ' + esc(b.name) + ' (' + b.had + ' in it)' : 'a new batch: ' + esc(b.name)) + forSaid : '';
  const backTo = back ? plural(back, "comes", "come") + " back fixed" : '';
  box.innerHTML = !rows.length ? "" : '<span class="rh"><b class="ok">' + good.length + ' read</b>' + (bad ? '<b class="bad">' + bad + ' left out</b>' : '') +
    (fresh.length && !b.seller ? '<span class="need">Name the seller to file ' + (back ? 'the new ones' : 'them') + '</span>'
      : to || backTo ? '<span class="to">' + [to, backTo].filter(Boolean).join(" \u00b7 ") + '</span>' : '') + '</span><ul>' +
    // The lines left out first: they are the ones that need the person.
    rows.filter(r => !r.x).concat(rows.filter(r => r.x)).slice(0, SHOW).map(r => r.back
      ? '<li class="ok back">' + SVG(ICON.back, 2.6) + '<span class="hp">' + esc(r.x.address) + '</span><span class="u">back fixed \u00b7 ' + backShort(r.x) + '</span></li>'
      : r.x ? '<li class="ok">' + SVG(ICON.tick, 3) + '<span class="hp">' + esc(r.x.address) + '</span><span class="u">' + (r.x.second || "no second factor") + '</span></li>'
      : '<li class="bad">' + SVG(ICON.cross, 3) + '<span class="hp">' + esc(r.raw.slice(0, 48)) + '</span><span class="u">line ' + r.line + ' ' + r.why + '</span></li>').join("") +
    (rows.length > SHOW ? '<li class="more">and ' + (rows.length - SHOW) + ' more</li>' : '') + '</ul>';
  const said = rows.length ? good.length + " read, " + bad + " left out" : "";
  if (el("cmp-sum").textContent !== said) el("cmp-sum").textContent = said;
  el("f-seller").classList.toggle("need", fresh.length > 0 && !b.seller);
  el("f-batch").textContent = b.name || "\u2014";
  const go = el("cmp-add");
  go.disabled = !good.length || (fresh.length > 0 && !b.seller);
  go.innerHTML = SVG(back && !fresh.length ? ICON.back : ICON.plus, 2.2) +
    (fresh.length && back ? "Add " + fresh.length + ", return " + back + " fixed" : back ? "Return " + back + " fixed" : good.length ? "Add " + good.length : "Add");
  // A paste on its way to the farm holds its button until the farm answers.
  if (adding) { go.disabled = true; go.textContent = fresh.length ? "Adding…" : "Returning…"; }
  const waiting = cmpOpen ? "" : [good.length && good.length + " waiting", bad && bad + " not read"].filter(Boolean).join(" \u00b7 ");
  el("cmpc").classList.toggle("draft", !!waiting);
  el("cmpc-sub").textContent = waiting || "paste a batch";
  el("cmpc").setAttribute("aria-label", "Add Gmails" + (waiting ? ", " + waiting : ""));
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
/* The paste goes to the farm, which judges every line again: new Gmails
   join the batch free in the queue, refused ones come back fixed with the
   details on their lines, and a line the farm refuses stays in the field
   with the ones this page could not read. */
el("cmp").addEventListener("submit", ev => {
  ev.preventDefault();
  if (adding) return;
  const rows = draftRows(), good = rows.filter(r => r.x), b = batchOf();
  const fresh = good.filter(r => !r.back), back = good.filter(r => r.back);
  if (!good.length) return;
  if (fresh.length && !b.seller) { el("f-seller").focus(); return; }
  if (good.length > 2000) { say("Paste at most 2000 Gmails at a time; this paste has " + good.length + ".", "amber"); return; }
  // A different product for a batch that has Gmails carries its unused ones with it.
  const carry = fresh.length && b.had && forPick !== b.forL ? GM.filter(e => famOf(e.sell) === b.name && canSwitch(e)).map(e => e.id) : [];
  const line = r => ({address: r.x.address, password: r.x.pass, key: r.x.key, recovery: r.x.rec});
  const lane = forPick, label = b.name;
  // The lines this paste carries: whatever else is typed while it is on its
  // way stays in the field.
  const went = new Set(good.map(r => r.x.address));
  adding = true;
  el("cmp-add").disabled = true;
  el("cmp-add").textContent = fresh.length ? "Adding\u2026" : "Returning\u2026";
  const took = a => {
    adding = false;
    const refused = a.refused || [], why = new Map(refused.map(r => [r.address, r.why]));
    const added = new Set(a.added || []), returned = new Set(a.returned || []);
    // Out of the field: the lines that went in. In it: the ones the farm
    // refused, the ones this page could not read, and anything typed since.
    const kept = el("paste").value.split(/\r?\n/).filter(l => {
      const x = l.trim() && parseLine(l.trim());
      return l.trim() && !(x && went.has(x.address) && !why.has(x.address));
    });
    el("paste").value = kept.join("\n");
    if (!kept.length) el("f-seller").value = "";
    if (added.size || returned.size) forTouched = false;
    added.forEach(id => flashIds.add(id));
    returned.forEach(id => { flashIds.add(id); litIds.add(id); });
    if (added.size) flashFam = label;
    if (added.size || returned.size) closeCmp(true);
    renderAll();
    renderRead();
    setTimeout(() => litIds.clear());
    const n = kept.length, first = refused[0];
    // "x@gmail.com is in the pool already"; "x@gmail.com: A key is 16 or more ..."
    const why1 = first ? first.why.replace(/[.\s]+$/, "") : "";
    const told = first ? first.address + (/^[a-z]/.test(why1) ? " " : ": ") + why1 : "";
    const said = [added.size ? plural(added.size, "Gmail") + " added to " + label + ", free in the queue" + (lane ? " and kept for " + PW[lane] : "") : "",
      returned.size ? plural(returned.size, "Gmail") + " back in the pool as fixed" : ""].filter(Boolean).join("; ");
    say((said ? said + "." : "") + (n ? (said ? " " : "") + n + (n === 1 ? " line was left out and waits" : " lines were left out and wait") + " in the field" +
      (told ? ": " + told : "") + "." : ""), !n);
  };
  press("/pools/gmail/add-batch", {rows: JSON.stringify(fresh.map(line)), back: JSON.stringify(back.map(line)),
    seller: fresh.length ? b.key : "", lane, carry: carry.join(",")}, took,
    a => { if ((a.refused || []).length) { took(a); return; } adding = false; renderRead(); say(a.note || a.said || "The farm did not take the paste.", "amber"); },
    // A long paste is written a line at a time: it is given two minutes.
    120000);
});
el("paste").addEventListener("input", renderRead);
el("f-seller").addEventListener("input", renderRead);
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
function closeMenu(back) {
  if (!menu) return;
  menu = ""; renderPop();
  document.querySelectorAll("[data-menu].on").forEach(b => b.classList.remove("on"));
  document.querySelectorAll('[data-menu][aria-expanded="true"]').forEach(b => b.setAttribute("aria-expanded", "false"));
  if (back && menuFrom && menuFrom.isConnected) menuFrom.focus({preventScroll: true});
  menuFrom = null;
}
const FOCUS_KEYS = ["data-seg", "data-k", "data-tick", "data-psw", "data-bsw", "data-keep", "data-menu", "data-fam", "data-open", "data-clear", "data-scope", "data-key", "data-prod", "data-defs", "data-flag", "data-pick",
  "data-bulk", "data-old", "data-what", "data-do", "data-arcf", "data-archall", "data-more"];
// A batch's key is typed by hand: escaped, so no seller's name breaks a selector.
const focusKey = a => !a || a === document.body || !a.hasAttribute ? "" : FOCUS_KEYS.map(k => a.hasAttribute(k) ? "[" + k + '="' + CSS.escape(a.getAttribute(k)) + '"]' : "").join("");
function refocus(key) {
  if (!key) return;
  const d = el("layer").querySelector(".draw"), b = (d && d.querySelector(key)) || document.querySelector(key);
  if (b && b !== document.activeElement) b.focus({preventScroll: true});
}
function slide(sw, on, then) {
  const fk = focusKey(sw), had = document.activeElement === sw;
  sw.setAttribute("aria-checked", String(on)); sw.disabled = true;
  setTimeout(() => { then(); if (had) refocus(fk); }, 190);
}
const toList = () => el("s-gm").scrollIntoView({behavior: calm ? "auto" : "smooth", block: "start"});
function onClick(ev) {
  const t = ev.target;
  if (menu && !t.closest(".pop") && !t.closest("[data-menu]")) closeMenu();
  if (cmpOpen && !t.closest("#addbox")) closeCmp();
  if (t.closest("#said .undo")) { if (undoFn) undoFn(); return; }
  if (t.closest("#cmpc")) { openCmp(); return; }
  const cm = t.closest("[data-cmp]");
  if (cm) { if (cm.dataset.cmp === "clear") { el("paste").value = ""; renderRead(); el("paste").focus(); } else closeCmp(true); return; }
  const fp = t.closest("[data-pick]");
  if (fp && t.closest("#cmp")) { forPick = fp.dataset.pick; forTouched = true; renderRead(); return; }
  if (t.classList.contains("scrim") || t.closest("[data-close]")) { closeLayer(); return; }
  const mn = t.closest("[data-menu]");
  if (mn) {
    if (menu === mn.dataset.menu) { closeMenu(); return; }
    closeMenu();
    menu = mn.dataset.menu; menuFrom = mn; mn.classList.add("on"); mn.setAttribute("aria-expanded", "true"); renderPop();
    if (!ev.detail) { const first = document.querySelector(".pop button"); if (first) first.focus(); }
    return;
  }
  const sw = t.closest("[data-bsw]");
  if (sw) { const on = sw.getAttribute("aria-checked") !== "true"; slide(sw, on, () => setFam(sw.dataset.bsw, on)); return; }
  const ps = t.closest("[data-psw]");
  if (ps) { const on = ps.getAttribute("aria-checked") !== "true"; slide(ps, on, () => apply(on ? "free" : "aside", new Set([+ps.dataset.psw]))); return; }
  const ed = t.closest("[data-ed]");
  if (ed) { if (ed.dataset.ed === "cancel") { editing = null; redrawDrawer(); } else saveEdit(ed.dataset.ed === "fixed"); return; }
  const d = t.closest("[data-do]");
  if (d) {
    const id = +d.dataset.id, k = d.dataset.do, from = menuFrom && menuFrom.getAttribute("data-menu");
    if (k === "story") { const back = menuFrom; closeMenu(); openDrawer(id, back); return; }
    // Edit, and Return - which asks what the fix changed first - open its details to edit.
    if (k === "edit") {
      const back = menuFrom, open = el("layer").querySelector('.draw[data-id="' + id + '"]'), e = GM.find(x => x.id === id);
      closeMenu();
      if (!e || !canEdit(e)) return;
      editing = {id};
      if (open) redrawDrawer(); else openDrawer(id, back);
      askSecret(id);
      return;
    }
    closeMenu();
    apply(k, new Set([id]));
    if (from && !ev.detail) refocus('[data-menu="' + CSS.escape(from) + '"]');
    return;
  }
  // The batch menu's items: each acts on the batch's Gmails it names.
  const fd = t.closest("[data-fdo]");
  if (fd) {
    const key = fd.dataset.fk, how = fd.dataset.fdo, f = famList().find(x => x.key === key);
    closeMenu(!ev.detail);
    if (!f) return;
    if (how === "show") { view.fam = key; renderFams(); renderList(); toList();
      if (!ev.detail) { const a = document.querySelector("#rows .addr"); if (a) a.focus({preventScroll: true}); }
      return; }
    if (how.startsWith("for:")) { flashFam = key; apply(how, new Set(f.ex.filter(canSwitch).map(e => e.id)), f.name); if (!ev.detail) refocus('[data-menu="' + CSS.escape("fam:" + key) + '"]'); return; }
    const pick = how === "free" || how === "mend" ? isOut : how === "aside" ? inPlay : how === "remove-all" ? canRemove
      : how === "remove-spent" ? (e => stateOf(e) === "spent") : () => true;
    if (how !== "copy") flashFam = key;
    if (how === "free" || how === "mend") litFam = key;
    apply(how.startsWith("remove") ? "remove" : how, new Set(f.ex.filter(pick).map(e => e.id)), f.name);
    if (!ev.detail) refocus('[data-menu="' + CSS.escape("fam:" + key) + '"]');
    return;
  }
  // A product on the operators' lane: only the Gmails used on it.
  const pq = t.closest(".key.pk");
  if (pq) { const l = pq.dataset.prod; view.prod = view.prod === l ? "" : l; whatWas.h = ""; renderWhat(); renderList(); return; }
  const ky = t.closest(".key");
  if (ky) {
    const side = ky.dataset.side, k = ky.dataset.key;
    if (side === "g") view.gk = view.gk === k ? "" : k; else view.vk = view.vk === k ? "" : k;
    whatWas.h = ""; renderWhat(); renderList(); setFocus(side, "");
    return;
  }
  const df = t.closest("[data-defs]");
  if (df) { view.defs = !view.defs; renderWhat(); refocus('[data-defs="1"]'); return; }
  const od = t.closest("[data-old]");
  if (od) { view.old = !view.old; famsDrawn = false; renderFams(); return; }
  const sc = t.closest("[data-scope]");
  if (sc) { view.scope = view.scope === sc.dataset.scope ? "" : sc.dataset.scope; renderStock(); renderList(); if (view.scope) toList(); return; }
  const sg = t.closest("[data-seg]");
  if (sg) {
    view[sg.dataset.seg] = sg.dataset.k;
    // Where it is over the list is the same filter as the Stock numbers.
    if (sg.dataset.seg === "scope") { renderStock(); renderList(); return; }
    renderWhat(); renderFams(); return;
  }
  const sk = t.closest("[data-sortk]");
  if (sk) { const k = sk.dataset.sortk; view.sort = k; view.dir = SORTS[k].dir; closeMenu(!ev.detail); renderList(); if (!ev.detail) refocus('[data-menu="sort"]'); return; }
  const fg = t.closest("[data-flag]");
  if (fg) { view.flag = view.flag === fg.dataset.flag ? "" : fg.dataset.flag; renderList(); return; }
  const clr = t.closest("[data-clear]");
  if (clr) {
    const c = clr.dataset.clear;
    if (c === "all") { Object.assign(view, {scope: "", flag: "", gk: "", vk: "", prod: "", fam: "", q: ""}); el("q").value = ""; }
    else view[c] = "";
    renderStock(); whatWas.h = ""; renderWhat(); renderFams(); renderList(); setFocus("g", ""); setFocus("v", "");
    return;
  }
  // A product's tile on a batch card: that batch's Gmails used on that product.
  const pt = t.closest(".pt");
  if (pt) { view.fam = pt.dataset.fk; view.prod = pt.dataset.l; whatWas.h = ""; renderWhat(); renderFams(); renderList(); toList(); return; }
  // A batch with nothing left in the pool: its Gmails, in the archive.
  const af = t.closest("[data-arcf]");
  if (af) { const f = famList().find(x => x.key === af.dataset.arcf); if (f) openArchive(af, "", {name: f.name, sellers: famSellers(f.key)}); return; }
  if (t.closest("[data-archall]")) { const q = el("arq"); openArchive(null, q ? q.value.trim() : "", null); return; }
  if (t.closest("[data-more]")) { rowLimit += PAGE_ROWS; renderRows(); if (ticked.size) renderSel(); if (!ev.detail) refocus('[data-more="1"]'); return; }
  const fc = t.closest("[data-fam]");
  if (fc) { view.fam = view.fam === fc.dataset.fam ? "" : fc.dataset.fam; renderFams(); renderList(); if (view.fam) toList(); return; }
  const bk = t.closest("[data-bulk]");
  if (bk) {
    if (bk.dataset.bulk === "clear") { ticked.clear(); renderRows(); renderSel(); }
    else if (bk.dataset.keep) { const kind = bk.dataset.bulk; slide(bk, kind === "free", () => apply(kind, new Set(ticked))); }
    else apply(bk.dataset.bulk, new Set(ticked), "", true);
    return;
  }
  const cp = t.closest("[data-copy]");
  if (cp) { copyText(cp.dataset.copy, "The " + (cp.dataset.what || "address").toLowerCase(), () => {
      const ic = cp.querySelector(".cp");
      if (ic) { ic.innerHTML = SVG(ICON.tick, 2.6); setTimeout(() => { if (ic.isConnected) ic.innerHTML = SVG(ICON.copy, 1.8); }, 1100); }
      cp.classList.add("just"); setTimeout(() => cp.classList.remove("just"), 1100); });
    return; }
  const tk = t.closest("[data-tick]");
  if (tk) { const id = +tk.dataset.tick; ticked.has(id) ? ticked.delete(id) : ticked.add(id); renderRows(); renderSel(); return; }
  if (t.closest("#all")) { const rows = shown(); rows.every(e => ticked.has(e.id)) ? rows.forEach(e => ticked.delete(e.id)) : rows.forEach(e => ticked.add(e.id)); renderRows(); renderSel(); return; }
  const th = t.closest("th.sortable");
  if (th) { if (view.sort === th.dataset.sort) view.dir *= -1; else { view.sort = th.dataset.sort; view.dir = SORTS[th.dataset.sort].dir; } renderList(); return; }
  const open = t.closest("[data-open]");
  if (open) { openDrawer(+open.dataset.open); return; }
  if (t.closest("#see-arch")) { openArchive(t.closest("#see-arch"), "", null); return; }
}
document.addEventListener("click", ev => {
  const a = document.activeElement, fk = focusKey(a);
  onClick(ev);
  if (fk && a && !a.isConnected) refocus(fk);
});
/* Pointing at a key, or reaching it with the keyboard, lights its share. */
const keyOf = n => n && n.closest && n.closest(".key:not(.pk)");
document.addEventListener("mouseover", ev => { const k = keyOf(ev.target); if (k) setFocus(k.dataset.side, k.dataset.key); });
document.addEventListener("mouseout", ev => { const k = keyOf(ev.target); if (k && !k.contains(ev.relatedTarget)) setFocus(k.dataset.side, ""); });
document.addEventListener("focusin", ev => { const k = keyOf(ev.target); if (k) setFocus(k.dataset.side, k.dataset.key); });
document.addEventListener("focusout", ev => { const k = keyOf(ev.target); if (k) setFocus(k.dataset.side, ""); });
/* Pointing at a product on the operators' lane puts only its presses on
   the lane; the lane comes back when the pointer leaves the products. */
const pkOf = n => n && n.closest && n.closest(".key.pk");
document.addEventListener("mouseover", ev => { const k = pkOf(ev.target); if (k && view.pv !== k.dataset.prod) { view.pv = k.dataset.prod; renderWhat(); } });
document.addEventListener("mouseout", ev => { const k = pkOf(ev.target); if (k && view.pv && !k.contains(ev.relatedTarget) && !pkOf(ev.relatedTarget)) { view.pv = ""; renderWhat(); } });
/* A card's ring: pointing at a part says it; leaving puts the ring back.
   A finger's tap holds it until the next tap anywhere else. */
const tapOf = n => n && n.closest && n.closest(".d2 .tap");
document.addEventListener("pointerover", ev => { const t = tapOf(ev.target); if (t) dialShow(t.closest(".d2"), t.dataset.k); });
document.addEventListener("pointerout", ev => { const t = tapOf(ev.target); if (t && ev.pointerType !== "touch" && !tapOf(ev.relatedTarget)) dialShow(t.closest(".d2"), ""); });
/* A product's tile beside a card's Done: pointing at it puts only that
   product's presses on the ring, until the pointer leaves the tiles. */
const ptOf = n => n && n.closest && n.closest(".d2 .pt");
document.addEventListener("pointerover", ev => { const p = ptOf(ev.target); if (p) dialProd(p.closest(".d2"), p.dataset.l); });
document.addEventListener("pointerout", ev => { const p = ptOf(ev.target); if (p && ev.pointerType !== "touch" && !ptOf(ev.relatedTarget)) dialProd(p.closest(".d2"), ""); });
document.addEventListener("pointerdown", ev => {
  if (tapOf(ev.target)) return;
  document.querySelectorAll(".d2.hot").forEach(d => dialShow(d, ""));
  if (!ptOf(ev.target)) document.querySelectorAll(".d2.prod").forEach(d => dialProd(d, ""));
});
const typing = n => !!n && !!n.matches && (n.matches("input, textarea, select") || n.isContentEditable);
document.addEventListener("keydown", ev => {
  // Ctrl+Z takes the last change back while its note stands; / finds a Gmail.
  if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && ev.code === "KeyZ" && undoFn && !typing(ev.target)) { ev.preventDefault(); undoFrom = focusKey(document.activeElement) || undoFrom; undoFn(); return; }
  if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey && !typing(ev.target) && !cmpOpen && !el("layer").querySelector(".draw")) {
    ev.preventDefault(); el("q").focus(); el("q").select(); return; }
  if (ev.key === "Escape") {
    if (ev.target === el("q")) ev.preventDefault();
    if (ev.target === el("q") && el("q").value) { el("q").value = ""; view.q = ""; renderList(); return; }
    if (cmpOpen) { closeCmp(true); return; }
    if (menu) { closeMenu(true); return; }
    if (editing && el("layer").querySelector(".draw")) { editing = null; redrawDrawer(); return; }
    closeLayer();
    return;
  }
  const pop = ev.target.closest && ev.target.closest(".pop[role='menu']");
  if (pop && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
    ev.preventDefault();
    const bs = [...pop.querySelectorAll("button")], i = bs.indexOf(ev.target);
    bs[(i + (ev.key === "ArrowDown" ? 1 : bs.length - 1)) % bs.length].focus();
    return;
  }
  const radio = ev.target.matches && ev.target.matches("[role=radio]") && ev.target.closest("[role=radiogroup]");
  if (radio && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(ev.key)) {
    ev.preventDefault();
    const rs = [...radio.querySelectorAll("[role=radio]")], i = rs.indexOf(ev.target), step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : -1;
    const next = rs[(i + step + rs.length) % rs.length];
    next.focus(); next.dispatchEvent(new MouseEvent("click", {bubbles: true, detail: 0}));
    return;
  }
  const draw = el("layer").querySelector(".draw");
  if (draw && ev.key === "Tab") {
    const f = [...draw.querySelectorAll("button,[tabindex='0'],input,textarea,select")].filter(x => !x.disabled && (x.offsetParent !== null || x.closest(".pop")));
    if (!f.length) return;
    ev.preventDefault();
    const i = f.indexOf(document.activeElement);
    f[i === -1 ? (ev.shiftKey ? f.length - 1 : 0) : (i + (ev.shiftKey ? -1 : 1) + f.length) % f.length].focus();
    return;
  }
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey) && cmpOpen) { ev.preventDefault(); el("cmp-add").click(); return; }
  if (ev.key === "Enter" && editing && ev.target.matches && ev.target.matches(".ed input")) {
    ev.preventDefault(); saveEdit(false); return; }
  if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches && ev.target.matches("[data-copy],[data-open],th.sortable")) { ev.preventDefault(); ev.target.click(); }
});
document.addEventListener("scroll", () => { if (menu) closeMenu(); }, {capture: true, passive: true});
window.addEventListener("resize", () => closeMenu());
el("q").addEventListener("input", ev => { view.q = ev.target.value.trim(); renderList(); });
document.addEventListener("focusin", ev => { if (cmpOpen && !ev.target.closest("#addbox")) closeCmp(); });

/* The page opens once: the numbers count up, the bars grow, the batches
   rise in turn - and after that it only answers presses. */
function countUp(node, to, ms, from) {
  from = from || 0;
  if (calm || to === from) return;
  const t0 = performance.now();
  const step = now => { if (!node.isConnected) return; const k = Math.min(1, (now - t0) / ms); node.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
  node.textContent = String(from);
  requestAnimationFrame(step);
  setTimeout(() => { if (node.isConnected) node.textContent = String(to); }, ms + 200);
}

/* ------------------------------------------------------------- the farm */
/* Every press goes to the farm and every answer comes back as JSON - the
   console's gates too (signed out, a stale session), by the header the
   Station's script sends. A press carries its own token, so the same press
   sent twice is one request and the next press is another. */
const REV = (document.querySelector('meta[name="gf-rev"]') || {}).content || "";
const HEAD = {"X-GF-Station": "page", "Accept": "application/json"};
let pressN = 0, busy = 0, adding = false;
/* No answer in twenty seconds is no answer: the request is let go, so the
   page never waits on it for ever. */
function timed(path, opts, ms) {
  const stop = new AbortController(), timer = setTimeout(() => stop.abort(), ms || 20000);
  return fetch(path, Object.assign({}, opts, {signal: stop.signal})).finally(() => clearTimeout(timer));
}
function send(path, fields, ms) {
  const body = new URLSearchParams(Object.assign({csrf: el("gf-csrf").value, press: "gm" + Date.now().toString(36) + (++pressN)}, fields));
  return timed(path, {method: "POST", body, headers: HEAD, credentials: "same-origin", cache: "no-store"}, ms)
    .then(r => r.json().catch(() => ({ok: false, note: "The farm answered " + r.status + " - reload the page and look again."})))
    .then(a => { if (!a.ok && a.go) location.href = a.go; return a; })
    .catch(() => ({ok: false, note: "The farm did not answer - look at the list before pressing again."}));
}
function get(path, tag) {
  return timed(path, {headers: Object.assign({}, HEAD, tag ? {"If-None-Match": tag} : {}), credentials: "same-origin", cache: "no-store"})
    .then(r => r.status === 304 ? {ok: true, same: true} : r.json().then(a => Object.assign(a, {etag: r.headers.get("ETag") || ""})).catch(() => ({ok: false})))
    .catch(() => ({ok: false}));
}
/* A press, sent: the farm answers with what it moved and the pool as it
   now stands, drawn at once. A press the farm's own queue took first is
   watched until it ends. Whatever had the focus when the answer came keeps
   it, in the control drawn in its place. */
function answered(a, run) {
  const a0 = document.activeElement, fk = focusKey(a0);
  // Newer than any state a poll already on its way will bring - but only
  // an answer that carries the pool may take a polled one's place.
  if (a.state) { held = null; drawnAt = ++seq; load(a.state); drawnText = JSON.stringify(a.state); }
  run();
  const now = document.activeElement;
  if (fk && (!now || now === document.body || !a0 || !a0.isConnected)) refocus(fk);
}
function fail(a, failed) {
  // What a press meant to light or flash does not, when it went nowhere.
  flashFam = ""; litFam = "";
  renderAll(); redrawDrawer(); renderRead();
  if (failed) failed(a);
}
function press(path, fields, done, failed, ms) {
  busy++;
  document.body.classList.add("sending");
  return send(path, fields, ms).then(a => {
    busy--;
    if (!busy) document.body.classList.remove("sending");
    if (a.status === "queued" || a.status === "running") { follow(a.req, done, failed, 0); return; }
    answered(a, () => { if (a.ok) done(a); else fail(a, failed); });
  });
}
/* The farm's queue took the press before this request could run it: the
   page asks after it every second and a half for a minute, through a
   missed answer or two, then says it is still queued - it will run, and
   the pool will show it. */
function follow(req, done, failed, tries) {
  busy++;
  document.body.classList.add("sending");
  setTimeout(() => get("/pools/gmail/state?req=" + req, "").then(a => {
    busy--;
    if (!busy) document.body.classList.remove("sending");
    const r = a.ok && a.reqs ? a.reqs[String(req)] : null;
    if ((!r || r.status === "queued" || r.status === "running") && tries < 40) { follow(req, done, failed, tries + 1); return; }
    const out = Object.assign({req}, r || {}, {ok: !!r && r.status === "done"});
    answered(a, () => {
      if (out.ok) done(out);
      else fail(r && r.status !== "queued" && r.status !== "running" ? out
        : {note: "The farm has it in its queue and will do it shortly; the list shows it when it is done."}, failed);
    });
  }), 1500);
}
/* The farm's answer, taken in: the pool, the day and the ranges. Ticks, the
   batch filter and an open drawer outlive it; a Gmail that left the pool
   leaves them. */
let asOf = "";
function load(d) {
  DATA = d;
  GM.length = 0;
  (d.rows || []).forEach(e => GM.push(e));
  archived = d.archived || 0;
  Object.assign(DAY, d.day || {});
  el("f-date").textContent = DAY.word || "";
  const f = d.firstSignin || "";
  RANGE_WORD.a = f ? "since " + (+f.slice(8, 10)) + " " + MONTHS[+f.slice(5, 7) - 1] : "so far";
  tick();
  asOf = NOWS;
  [...ticked].forEach(id => { if (!GM.some(e => e.id === id)) ticked.delete(id); });
  if (view.fam && !GM.some(e => famOf(e.sell) === view.fam) && !Object.keys(DATA.batch || {}).some(k => famOf(k) === view.fam)) view.fam = "";
}
/* A state the page was not asked for - the farm moving on its own: drawn,
   and every Gmail that changed glows once. */
const sig = e => JSON.stringify([e.st, e.next, e.tries, e.serial, e.for, e.key, e.rec, e.note, (e.t || []).length, (e.p || []).length, e.back ? e.back.at : "", (e.log || []).length]);
function take(d) {
  const was = new Map(GM.map(e => [e.id, sig(e)])), fk = focusKey(document.activeElement);
  load(d);
  const moved = new Set();
  GM.forEach(e => { if (was.has(e.id) && was.get(e.id) !== sig(e)) { flashIds.add(e.id); moved.add(e.id); } });
  renderAll();
  // The open drawer is drawn again only if its Gmail moved (or left): a
  // hand on a copy line or a selected password is not disturbed for
  // another Gmail's news. Moved, its details are read again.
  const open = el("layer").querySelector(".draw:not([data-arch])");
  if (open && (moved.has(+open.dataset.id) || !was.has(+open.dataset.id) || !GM.some(e => e.id === +open.dataset.id))) {
    SEC.delete(+open.dataset.id);
    redrawDrawer();
    if (el("layer").querySelector(".draw:not([data-arch])")) askSecret(+open.dataset.id);
  }
  renderRead();
  refocus(fk);
}
/* The page asks the farm every twenty seconds (a minute when hidden); an
   unchanged pool is a 304. A state that arrives while a menu is open, a
   press is on its way or a form is being filled waits for it. */
let pollTimer = 0, polling = false, etag = "", held = null, heldText = "", seq = 0, drawnAt = 0, drawnText = "", newRev = "";
function poll(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(refresh, ms); }
const quiet = () => !menu && !busy && !adding && !editing && !document.querySelector(".sw:disabled");
const idle = () => quiet() && !cmpOpen && !el("paste").value.trim() && !el("layer").firstChild && !ticked.size && !narrowed();
function refresh() {
  if (polling) return;
  polling = true;
  const mine = ++seq;
  get("/pools/gmail/state", etag).then(a => {
    polling = false;
    let next = 20000, leaving = false;
    // Whatever goes wrong in one answer, the page keeps asking.
    try {
      // Signed out meanwhile: to the sign-in, not a page that quietly stops.
      if (!a.ok && a.go) { leaving = true; location.href = a.go; return; }
      // Asked before a press answered: older than what the page shows.
      if (mine < drawnAt) { next = 1000; return; }
      if (a.ok && !a.same) {
        if (a.etag) etag = a.etag;
        if (a.rev && REV && a.rev !== REV) newRev = a.rev;
        if (a.state) { const text = JSON.stringify(a.state); if (text !== drawnText) { held = a.state; heldText = text; } }
      }
      // A new build of this page reloads it the first moment nobody is
      // in the middle of something - not only on the answer that said so.
      if (newRev && idle()) { leaving = true; location.reload(); return; }
      // Asked and answered: the pool is as drawn as of now.
      if (a.ok) { tick(); asOf = NOWS; el("stockwhen").textContent = "as of " + stamp(asOf) + " Tehran"; }
      if (held && quiet()) { const d = held; drawnText = heldText; held = null; drawnAt = mine; take(d); }
      next = held ? 1500 : document.hidden ? 60000 : 20000;
    } finally {
      if (!leaving) poll(next);
    }
  });
}
/* The time a Gmail has waited on its phone, and every "back at" and "today",
   move with the clock: the numbers and the list are drawn again each
   minute while nobody is in the middle of something. */
setInterval(() => {
  tick();
  if (document.hidden || !quiet() || cmpOpen) return;
  const fk = focusKey(document.activeElement);
  renderStock();
  renderList();
  refocus(fk);
}, 60000);

/* The archive: every Gmail spent or archived, newest first, in a drawer of
   its own, with a box to find one by its address or its batch. */
const ARCH_WAS = r => r.was === "used" ? "spent" + (r.used ? " " + day(r.used) : "")
  : r.was === "set_aside" ? "archived while set aside" : !r.was ? "archived while free"
  : "archived after " + GLOW[gkey(r.was, false)] + " (" + rawWord(r.was) + ")";
function archLines(rows) {
  return rows.length ? rows.map((r, i) => '<div style="--i:' + i + '"><b>' + esc(r.a) + '</b><span class="t">' + esc(stamp(r.at)) + '</span><small>' +
    esc([famOf(r.f), ARCH_WAS(r), r.by && "by " + r.by].filter(Boolean).join(" \u00b7 ")) + '</small></div>').join("")
    : '<div class="none">Nothing in the archive matches that.</div>';
}
let archAsk = 0;
/* The batch the archive drawer is narrowed to - {name, sellers} - or null
   for the whole archive. A search within it stays within it. */
let archFam = null;
const archScope = fam => fam ? '<div class="arscope"><span>Batch <b>' + esc(fam.name) + '</b></span><button class="link" type="button" data-archall="1">Show the whole archive</button></div>' : "";
function openArchive(from, q, fam) {
  const mine = ++archAsk;
  // Given, the drawer is narrowed to that batch or (null) opened on all of
  // it; not given - a search typed in the drawer - it keeps its scope.
  const scope = fam === undefined ? archFam : fam;
  const ps = [q ? "q=" + encodeURIComponent(q) : ""].concat(scope ? scope.sellers.map(s => "seller=" + encodeURIComponent(s)) : []).filter(Boolean);
  get("/pools/gmail/archive" + (ps.length ? "?" + ps.join("&") : "")).then(a => {
    if (mine !== archAsk) return;
    if (!a.ok) { if (a.go) { location.href = a.go; return; } say(a.note || "The archive could not be read just now.", "amber"); return; }
    const rows = a.rows || [], total = a.total || 0, hits = a.matched == null ? rows.length : a.matched;
    const head = !total ? (scope ? "Nothing of " + scope.name + " is in the archive." : "Nothing has been archived yet.")
      : q ? (!hits ? "Nothing in the archive matches that." : hits > rows.length ? "The newest " + rows.length + " of the " + hits + " that match"
        : hits === 1 ? "1 Gmail of " + total + " matches" : hits + " Gmails of " + total + " match")
      : scope ? (rows.length < total ? "The newest " + rows.length + " of the " + total : "All " + total) + " Gmails of " + scope.name + ", newest first: spent on a phone, or archived by a person."
      : (rows.length < total ? "The newest " + rows.length + " of " + total : "All " + total) + ", newest first: spent on a phone, or archived by a person.";
    archFam = scope;
    const open = el("layer").querySelector(".draw[data-arch]");
    if (open) {
      open.querySelector(".why").textContent = head;
      const sc = open.querySelector(".arscope");
      if (sc) sc.remove();
      if (scope) open.querySelector(".arq").insertAdjacentHTML("beforebegin", archScope(scope));
      open.querySelector(".arl").innerHTML = archLines(rows);
      return;
    }
    // Opened by a press, onto a page with no drawer: a search's late answer
    // for a drawer closed meanwhile is dropped.
    if (!from || el("layer").querySelector(".draw")) return;
    drawerFrom = from;
    el("layer").innerHTML = '<div class="scrim"></div><aside class="draw opening" role="dialog" aria-modal="true" aria-label="Archived Gmails" data-arch="1">' +
      '<header><span class="id"><b>Archived Gmails</b><small class="why">' + esc(head) + '</small></span><button class="xbtn" type="button" data-close="1" aria-label="Close">' + SVG(ICON.cross, 2.2) + '</button></header>' +
      '<div class="body">' + archScope(scope) + '<label class="arq">' + SVG('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>', 2) +
      '<input id="arq" type="search" placeholder="Find an address or a batch" autocomplete="off" spellcheck="false" aria-label="Find an archived Gmail"></label>' +
      '<div class="arl">' + archLines(rows) + '</div></div></aside>';
    document.documentElement.classList.add("locked");
    el("layer").querySelector(".xbtn").focus({preventScroll: true});
    const d = el("layer").querySelector(".draw");
    setTimeout(() => d.classList.remove("opening"), 1200);
  });
}
let archTimer = 0;
document.addEventListener("input", ev => {
  if (ev.target.id !== "arq") return;
  clearTimeout(archTimer);
  const q = ev.target.value.trim();
  archTimer = setTimeout(() => openArchive(null, q), 280);
});

/* The page opens once: the numbers count up, the bars grow, the batches
   rise in turn - and after that it answers presses, and the farm. */
load(DATA);
drawnText = JSON.stringify(DATA);
renderAll();
renderRead();
document.querySelectorAll(".num .dial b").forEach(b => countUp(b, +b.textContent, 900));
setTimeout(() => document.querySelector("main").classList.remove("boot"), 1500);
poll(20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(300); });
