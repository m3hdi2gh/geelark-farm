// A document the Station's script can run in: the real templates, filled
// the way `station_pages.py` fills them, with the browser's clock, network,
// stream, clipboard and windows under the test's hand.
//
// linkedom gives the tree and the events. What it lacks, or what a test
// must drive, is shimmed here and only here: `document.activeElement`,
// timers on a clock the test moves, `Date.now`, `fetch`, `EventSource`,
// `window.open`, `navigator.clipboard` and `sendBeacon`, `Element.animate`
// and the layout calls (`getBoundingClientRect`, `scrollIntoView`).
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';
import {webcrypto} from 'node:crypto';
import vm from 'node:vm';
import {parseHTML} from 'linkedom';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = join(HERE, '..', '..', 'src', 'geelark_farm', 'web', 'static');
export const SCRIPT = readFileSync(join(STATIC, 'station.js'), 'utf8');
const read = (name) => readFileSync(join(STATIC, name), 'utf8');

/** The island `station_pages._island` writes. */
export function island(obj) {
  return JSON.stringify(obj).replace(/</g, '\\u003c')
    .replace(new RegExp(String.fromCharCode(0x2028), 'g'), '\\u2028')
    .replace(new RegExp(String.fromCharCode(0x2029), 'g'), '\\u2029');
}

const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;'}[c]));

/** A template filled the way the server fills it. */
export function documentFor(page, state, {rev = 'test'} = {}) {
  const tpl = read(page === 'live' ? 'station_live.html' : 'station_page.html');
  const values = {
    TITLE: 'IranSpoty Station', FAVICON: 'data:,', CSS: '/s/station-x.css',
    JS: '/s/station-x.js', REV: rev, CSRF: 'tok', BRAND: read('station_brand.svg').trim(),
    ICONS: read('station_icons.html').trim(), STATE: island(state),
    NAME: esc((state.me && state.me.name) || ''),
    INITIAL: esc((state.me && state.me.initial) || ''),
    SERIAL: esc(state.serial || ''), LANE: state.lane || 'gpt',
  };
  return tpl.replace(/\{\{([A-Z]+)\}\}/g, (_, k) => values[k]);
}

/** Answers a fake fetch hands back, from `{status, body, headers}`. */
function reply({status = 200, body = '', headers = {}, type = 'basic'}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  const h = Object.assign(
    typeof body === 'string' ? {} : {'Content-Type': 'application/json; charset=utf-8'},
    headers);
  return {
    ok: status >= 200 && status < 300, status, type,
    headers: {get: (name) => {
      for (const k of Object.keys(h)) if (k.toLowerCase() === String(name).toLowerCase()) return h[k];
      return null;
    }},
    text: () => Promise.resolve(text),
    json: () => Promise.resolve().then(() => JSON.parse(text)),
  };
}

/** The Station (or a Live tab) with its script running, and the levers. */
export function pageIn(page, state, opts = {}) {
  const {window} = parseHTML(documentFor(page, state, opts));
  const doc = window.document;
  EVENTS.set(doc, window.CustomEvent);

  // --- the clock: Date.now and every timer run on it.
  const clock = {now: opts.now || Date.UTC(2026, 8, 29, 10, 0, 0)};
  const timers = [];
  let nextId = 1;
  function addTimer(fn, ms, every) {
    const t = {id: nextId++, fn, due: clock.now + Math.max(0, ms || 0), every};
    timers.push(t);
    return t.id;
  }
  function clearTimer(id) {
    const i = timers.findIndex((t) => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  }
  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(clock.now); }
    static now() { return clock.now; }
  }

  // --- focus, which linkedom does not track.
  let focused = null;
  Object.defineProperty(doc, 'activeElement', {configurable: true, get: () => focused});
  window.HTMLElement.prototype.focus = function () { focused = this; };
  window.HTMLElement.prototype.blur = function () { if (focused === this) focused = null; };
  const scrolled = [];
  window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this); };
  const anims = [];
  window.HTMLElement.prototype.animate = function (frames, o) {
    const a = {el: this, frames, o, onfinish: null};
    anims.push(a);
    return a;
  };
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return {left: 10, top: 10, width: opts.noLayout ? 0 : 20, height: 20};
  };
  Object.defineProperty(doc, 'visibilityState', {configurable: true, get: () => 'visible'});

  // --- the network.
  const fetches = [];
  function fakeFetch(url, init = {}) {
    const body = init.body ? Object.fromEntries(new URLSearchParams(String(init.body))) : null;
    const call = {url: String(url), init, body, headers: init.headers || {}};
    fetches.push(call);
    const answer = opts.answer ? opts.answer(call) : null;
    if (answer instanceof Error) return Promise.reject(answer);
    if (!answer) return Promise.resolve(reply({status: 204, body: ''}));
    if (answer.after) return answer.after.then(() => (answer.error ? Promise.reject(answer.error) : reply(answer)));
    return Promise.resolve(reply(answer));
  }
  const streams = [];
  function FakeEventSource(url) {
    this.url = url; this.readyState = 1; this.onmessage = null;
    this.emit = (data) => this.onmessage && this.onmessage({data: String(data)});
    this.close = () => { this.readyState = 2; };
    streams.push(this);
  }

  // --- windows, the clipboard, the beacon.
  const opened = [];
  const windows = opts.windows || {};
  const beacons = [];
  const clipboard = [];
  const went = [];
  const replaced = [];
  const selections = [];
  const location = {pathname: opts.path || (page === 'live' ? '/station/phones/' + state.serial : '/station'),
    search: '', hash: opts.hash || '', href: 'https://farm.test/',
    assign: (u) => went.push(String(u)), reload: () => went.push('reload')};
  const winListeners = {};

  const sandbox = {
    document: doc, location,
    history: {replaceState: (a, b, u) => { replaced.push(u); location.hash = ''; }},
    navigator: {
      clipboard: {writeText: (t) => { clipboard.push(t);
        return opts.clipboardFails ? Promise.reject(new Error('denied')) : Promise.resolve(); }},
      sendBeacon: (u, b) => { beacons.push({url: u, body: b}); return true; },
    },
    console,
    setTimeout: (fn, ms) => addTimer(fn, ms, 0), clearTimeout: clearTimer,
    setInterval: (fn, ms) => addTimer(fn, ms, ms || 1), clearInterval: clearTimer,
    fetch: fakeFetch, URLSearchParams, URL, EventSource: opts.noStream ? undefined : FakeEventSource,
    Event: window.Event, matchMedia: (q) => ({matches: !!(opts.media && opts.media[q]), addEventListener() {}}),
    crypto: opts.noSubtle ? {getRandomValues: (b) => webcrypto.getRandomValues(b)} : webcrypto,
    getSelection: () => ({removeAllRanges() {}, addRange(r) { selections.push(r); }}),
    requestAnimationFrame: (fn) => addTimer(fn, 16, 0), performance: {now: () => clock.now},
    Uint8Array, Blob, TextEncoder, btoa, Node: window.Node, HTMLElement: window.HTMLElement,
    innerHeight: 900, innerWidth: 1400, scrollY: 0, sessionStorage: {getItem: () => null, setItem() {}},
    Date: FakeDate,
    addEventListener: (type, fn) => { (winListeners[type] = winListeners[type] || []).push(fn); },
    open: (url, name) => {
      opened.push({url, name});
      const w = windows[name] || (windows[name] = {name,
        location: {href: 'about:blank', pathname: 'blank', hash: ''}, focused: 0,
        focus() { this.focused++; }});
      return w;
    },
    scrollTo: () => {},
  };
  sandbox.window = sandbox;
  sandbox.name = '';
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT, sandbox, {filename: 'station.js'});

  /** Move the clock, running every timer that falls due on the way. */
  async function advance(ms) {
    const end = clock.now + ms;
    for (;;) {
      await settle();
      timers.sort((a, b) => a.due - b.due || a.id - b.id);
      const t = timers[0];
      if (!t || t.due > end) break;
      clock.now = Math.max(clock.now, t.due);
      if (t.every) t.due += t.every; else timers.shift();
      t.fn();
    }
    clock.now = end;
    await settle();
  }
  function fireWin(type, ev = {}) { for (const fn of winListeners[type] || []) fn(ev); }
  function finishSparks() {
    for (const a of anims.splice(0)) if (a.onfinish) a.onfinish();
  }
  return {win: sandbox, doc, clock, fetches, streams, opened, windows, beacons, clipboard,
    went, replaced, scrolled, anims, selections, advance, fireWin, finishSparks,
    focused: () => focused, focus: (el) => { focused = el; },
    $: (sel) => doc.querySelector(sel), $$: (sel) => [...doc.querySelectorAll(sel)]};
}

/** An event linkedom will carry, with the fields a handler reads. */
const EVENTS = new WeakMap();
export function fire(el, type, extra = {}) {
  const Custom = EVENTS.get(el.ownerDocument || el);
  const ev = new Custom(type, {bubbles: true, cancelable: true, detail: extra.detail || 0});
  for (const k of Object.keys(extra)) if (k !== 'detail') ev[k] = extra[k];
  el.dispatchEvent(ev);
  return ev;
}

/** A click, as the browser gives it: detail 1 for a single click. */
export const click = (el, detail = 1) => fire(el, 'click', {detail});

/** Let every pending promise settle. */
export async function settle() {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
}
