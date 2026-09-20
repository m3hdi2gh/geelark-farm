// A document the console's script can run in, so its behaviour can be
// tested instead of its spelling.
//
// Every test that touched this script asserted a substring of its
// source, and that is how `var typing` came to be computed in
// `mayRedraw` and never read: the guard that holds a redraw while
// somebody is typing was dead from September to 2026-09-20, under a
// green test asserting `"function mayRedraw()" in script`.
//
// linkedom gives the tree and the events; the two things it does not
// have are the two the console leans on hardest, so they are shimmed
// here and only here: `document.activeElement`, which every redraw
// guard asks about, and `<dialog>`'s showModal/close, which the editor
// is built out of.
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';
import vm from 'node:vm';
import {parseHTML} from 'linkedom';

const HERE = dirname(fileURLToPath(import.meta.url));
export const SCRIPT = readFileSync(
  join(HERE, '..', '..', 'src', 'geelark_farm', 'web', 'static', 'dash.js'),
  'utf8');

/** A page with the script running in it, and the levers a test needs. */
export function consoleIn(html, opts = {}) {
  const {window} = parseHTML(
    `<!doctype html><html><head>
       <meta name="gf-rev" content="test">
     </head><body><div class="shell"><main>${html}</main></div></body></html>`);
  const doc = window.document;

  // --- activeElement, which linkedom does not track.
  let live = null;
  Object.defineProperty(doc, 'activeElement', {
    configurable: true, get: () => live,
  });
  const focusable = ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'];
  for (const el of doc.querySelectorAll('*')) shimFocus(el);
  function shimFocus(el) {
    if (el.focus) return;
    el.focus = () => { live = el; };
    el.blur = () => { if (live === el) live = null; };
  }
  window.__focus = (el) => { live = el; };
  window.__focused = () => live;

  // --- <dialog>, which the editor is built out of.
  for (const dlg of doc.querySelectorAll('dialog')) shimDialog(dlg);
  function shimDialog(dlg) {
    let open = dlg.hasAttribute('open');
    Object.defineProperty(dlg, 'open', {
      configurable: true,
      get: () => open,
      set: (v) => { open = !!v; if (open) dlg.setAttribute('open', '');
                    else dlg.removeAttribute('open'); },
    });
    dlg.showModal = () => { dlg.open = true; };
    dlg.close = () => { dlg.open = false; };
  }
  window.__shimDialog = shimDialog;

  // --- form.elements, which the editor is filled through, and
  //     form.requestSubmit, which the confirm bubble presses.
  for (const form of doc.querySelectorAll('form')) shimForm(form);
  function shimForm(form) {
    Object.defineProperty(form, 'elements', {
      configurable: true,
      get() {
        const out = [];
        for (const el of form.querySelectorAll(
                 'input, select, textarea, button')) {
          out.push(el);
          if (el.name && !(el.name in out)) out[el.name] = el;
        }
        return out;
      },
    });
    if (!form.requestSubmit) form.requestSubmit = () => fireOn(form, 'submit');
    // linkedom keeps these as attributes and not as properties, and the
    // submit handler's first test is `form.method`, so without them
    // every press falls straight through to the browser.
    for (const name of ['method', 'action', 'target']) {
      if (form[name] !== undefined) continue;
      Object.defineProperty(form, name, {
        configurable: true,
        get: () => form.getAttribute(name) || '',
      });
    }
  }
  window.__shimForm = shimForm;
  function fireOn(el, type) {
    const ev = doc.createEvent('Event');
    ev.initEvent(type, true, true);
    el.dispatchEvent(ev);
  }

  // --- Node.contains, which the submit handler gates on.
  const proto = Object.getPrototypeOf(doc.querySelector('main'));
  if (!proto.contains) {
    proto.contains = function (other) {
      for (let at = other; at; at = at.parentNode) if (at === this) return true;
      return false;
    };
  }

  // --- what the script reaches for that a page normally has.
  const fetches = [];
  window.fetch = (url, init) => {
    const call = {url: String(url), init: init || {}};
    fetches.push(call);
    const answer = opts.answer ? opts.answer(call) : null;
    if (!answer) return Promise.resolve(reply({status: 204, body: ''}));
    if (answer instanceof Error) return Promise.reject(answer);
    return Promise.resolve(reply(answer));
  };
  function reply({status = 200, body = '', url = '', textThrows = false}) {
    return {
      ok: status >= 200 && status < 300, status, url, redirected: false,
      // `textThrows` is the case that matters most: the command reached
      // the server and was carried out, and the page then failed to
      // read the answer. Anything the handler does about that must not
      // be "send it again".
      text: () => textThrows
        ? Promise.reject(new Error('the answer could not be read'))
        : Promise.resolve(body),
    };
  }
  window.__fetches = fetches;

  window.EventSource = function () { this.close = () => {}; };
  window.sessionStorage = {
    getItem: () => null, setItem: () => {}, removeItem: () => {},
  };
  window.navigator = window.navigator || {};
  window.getSelection = () => ({isCollapsed: true, toString: () => ''});
  window.DOMParser = window.DOMParser || class {
    parseFromString(text) { return parseHTML(text).document; }
  };
  // `location.assign` is how the script gives up and leaves; a test
  // wants to know it happened, not to go anywhere.
  const went = [];
  window.location = {pathname: '/', search: '', hash: '', href: 'http://t/',
                     assign: (u) => went.push(String(u)),
                     reload: () => went.push('reload')};
  window.__went = went;
  window.history = {replaceState: () => {}};

  // Timers a test drives by hand: the script arms redraws with
  // setTimeout, and a harness that waited for them in real time would
  // be a harness nobody runs.
  const timers = [];
  window.setTimeout = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
  window.clearTimeout = (id) => { if (timers[id - 1]) timers[id - 1].fn = null; };
  window.__runTimers = () => {
    const due = timers.splice(0, timers.length);
    for (const t of due) if (t.fn) t.fn();
  };
  window.__timers = timers;

  // A plain sandbox rather than linkedom's own window contextified.
  // That window is a Proxy that manufactures names on access, so a
  // value put on it did not reach the name the script resolves - and
  // `e.target instanceof HTMLFormElement`, the submit handler's first
  // gate, was false for a real form. Every press fell straight through
  // to the browser and the harness ran while testing nothing, which is
  // the failure mode this whole file exists to end.
  const sandbox = {
    document: doc, location: window.location, history: window.history,
    navigator: window.navigator, sessionStorage: window.sessionStorage,
    fetch: window.fetch, EventSource: window.EventSource,
    DOMParser: window.DOMParser, FormData: FakeFormData,
    URLSearchParams: window.URLSearchParams, Event: window.Event,
    MouseEvent: window.MouseEvent || window.Event,
    Option: window.Option, getSelection: window.getSelection,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    innerHeight: 900, innerWidth: 1400,
    addEventListener: window.addEventListener.bind(window),
    removeEventListener: window.removeEventListener.bind(window),
    // What the tagName test stands in for: linkedom has no
    // HTMLFormElement at all, every element is an HTMLElement.
    HTMLFormElement: {[Symbol.hasInstance]:
      (el) => !!el && el.tagName === 'FORM'},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT, sandbox, {filename: 'dash.js'});
  // Everything a test reaches for hangs off the window it was given.
  for (const key of Object.keys(window))
    if (key.startsWith('__')) sandbox[key] = window[key];
  sandbox.__doc = doc;
  return sandbox;
}

/** What the browser hands a form's fields over as.

 *  linkedom's own FormData will not take a linkedom form, and
 *  `new URLSearchParams` is happy with anything that yields pairs. */
class FakeFormData {
  constructor(form) {
    this._pairs = [];
    if (!form) return;
    for (const el of form.querySelectorAll('input, select, textarea')) {
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (!el.name || el.disabled) continue;
      if ((type === 'checkbox' || type === 'radio') && !el.checked) continue;
      this._pairs.push([el.name, el.value == null ? '' : String(el.value)]);
    }
  }
  append(name, value) { this._pairs.push([name, String(value)]); }
  get(name) {
    const hit = this._pairs.find((pair) => pair[0] === name);
    return hit ? hit[1] : null;
  }
  *[Symbol.iterator]() { yield* this._pairs; }
}

/** A DOM event linkedom will carry, since it has no constructors for
 *  the ones the console uses. */
export function fire(el, type, extra = {}) {
  const doc = el.ownerDocument || el;
  const ev = doc.createEvent ? doc.createEvent('Event')
                             : new (el.ownerDocument.defaultView.Event)(type);
  if (ev.initEvent) ev.initEvent(type, true, true);
  else { ev.type = type; }
  Object.assign(ev, extra);
  el.dispatchEvent(ev);
  return ev;
}

/** Let every pending promise settle. */
export const settle = () => new Promise((r) => setImmediate(r));
