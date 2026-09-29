// The Station's script, run rather than read.
//
//     node --test tests/dash/
//
// Each case drives the real templates with the script running in them,
// and a farm faked at the fetch: what a press posts, what the page draws
// from the answer, and what it must never do (post twice, bring a closed
// card back, pull the focus out of the dialog).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {pageIn, click, fire, settle, island} from './station_harness.mjs';

const T0 = Date.UTC(2026, 8, 29, 10, 0, 0);
const MIN = 60000;

function phone(serial, over = {}) {
  return Object.assign({
    serial, lane: 'gpt', power: 'off', exit: 'PC2', taken_at: T0 - 10 * MIN,
    idle_since: T0 - 10 * MIN, watching: false, live: false, tab_seen: false,
    bare: false, gmail: 'g' + serial + '@gmail.com', pw: 'pw-' + serial,
    totp: 'JBSWY3DPEHPK3PXP', arrived: 'take', wish: null, called_off: false,
    last: null}, over);
}

function state(over = {}) {
  return Object.assign({
    v: 1, rev: 'test', now: T0, hold_minutes: 60, late_minutes: 15,
    me: {id: 7, name: 'Sara', user: 'sara', initial: 'S', since: 'since 12 Sep 2026',
         pw: 'Changed 12 days ago', daypart: 'morning'},
    may: {take: true, ip: true, build: true},
    shelves: {
      gpt: {ready: 3, building: 1, eta_at: T0 + 5 * MIN, etas: [T0 + 5 * MIN], late: 0,
            typical_min: 6, line: null},
      spotify: {ready: 0, building: 0, eta_at: null, etas: [], late: 0, typical_min: 7,
                line: null}},
    tally: {done: 2, decline: 1, or: 0, auth: 0, failed: 0, all: 3},
    phones: [phone('5073'), phone('5061', {lane: 'spotify', power: 'on', live: true})],
    builds: [], today: [], notes: [],
    build_form: {gmails_left: 12, free_ips: {gpt: 5, spotify: 3, other: 8}, stopped: false,
                 typical_min: {gpt: 6, spotify: 7, other: 6}},
  }, over);
}

const ok = (body) => ({status: 200, body: Object.assign({ok: true}, body)});

test('first paint draws the cards, the bar and the ghosts from #gf-state', async () => {
  const st = state({
    shelves: Object.assign(state().shelves, {spotify: {ready: 0, building: 1,
      eta_at: T0 + 3 * MIN, etas: [T0 + 3 * MIN], late: 0, typical_min: 7,
      line: {id: 41, position: 1, joined_at: T0 - MIN}}}),
    builds: [{id: 123, lane: 'other', stage: 'queued', eta_at: null, late: false,
              typical_min: 6, bare: true, called_off: false, reason: '',
              chips: ['bare phone', 'any IP']}],
  });
  const p = pageIn('station', st);
  await settle();
  const cols = p.$$('#bench > section');
  assert.deepEqual(cols.map((c) => c.id), ['p-5073', 'p-5061', 'l-spotify', 'w-123']);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Ready');
  assert.equal(p.$('#p-5061 [data-k="st"]').textContent, 'On');
  assert.equal(p.$('#p-5073 [data-k="exit"]').textContent, 'PC2');
  assert.equal(p.$('#p-5073 [data-k="gmail"]').textContent, 'g5073@gmail.com');
  assert.equal(p.$('#p-5073 [data-k="age"]').textContent, '10 min');
  assert.equal(p.$('#t-all').textContent, '3');
  assert.equal(p.$('#t-done').textContent, '2');
  assert.equal(p.$('#take-gpt .c').textContent, '3');
  assert.equal(p.$('#take-gpt .sub').textContent, '+1 in 5 min');
  assert.equal(p.$('#take-spotify .w').textContent, 'In line');
  assert.equal(p.$('#take-spotify').disabled, true);
  assert.equal(p.$('#l-spotify .g-t b').textContent, 'You are first in line');
  assert.equal(p.$('#w-123 .g-t b').textContent, 'Building your phone');
  assert.deepEqual(p.$$('#w-123 .g-chips span').map((s) => s.textContent), ['bare phone', 'any IP']);
  assert.equal(p.$('#empty').hidden, true);
  assert.equal(p.win.name, 'gf-station');
  assert.equal(p.streams.length, 1, 'it listens to the farm');
  assert.equal(p.streams[0].url, '/live');
});

// ------------------------------------------------------------- helpers
async function waitFor(check, what) {
  for (let i = 0; i < 200; i++) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail('never happened: ' + what);
}
const posts = (p, path) => p.fetches.filter((f) => f.url === path && f.init.method === 'POST');
const gets = (p, path) => p.fetches.filter((f) => f.url === path && f.init.method === 'GET');
function without(st, serial) {
  return Object.assign({}, st, {phones: st.phones.filter((x) => x.serial !== serial)});
}
/** A tick of the farm's stream, then the pull it causes. */
async function pullNow(p) {
  p._rev = (p._rev || 100) + 1;
  p.streams[0].emit(p._rev);
  await p.advance(2100);
}
function primeStream(p) { p.streams[0].emit(100); p._rev = 100; }
const toast = (p) => p.$('#said').textContent;

// ------------------------------------------------------------ verdicts
test('a verdict posts once, on the second tap after 350 ms', async () => {
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/state' ? ok({said: 'declined', state: without(state(), '5073')}) : null});
  await settle();
  const key = p.$('#p-5073 .verdict [data-v="decline"]');
  click(key);
  assert.equal(key.querySelector('.l').textContent, 'Tap again');
  await p.advance(100);
  click(key);                                   // too soon: a double click
  assert.equal(posts(p, '/phones/5073/state').length, 0);
  await p.advance(300);
  click(key);
  await settle();
  const sent = posts(p, '/phones/5073/state');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.state, 'decline');
  assert.equal(sent[0].body.sure, '1');
  assert.equal(sent[0].body.where, 'station');
  assert.equal(sent[0].body.csrf, 'tok');
  assert.match(sent[0].body.press, /^[A-Za-z0-9_-]{8}$/);
  assert.equal(sent[0].headers['X-GF-Station'], 'page');
  assert.equal(toast(p), 'Phone 5073 closed as Decline. It is deleted in a moment.');
  assert.ok(p.$('#p-5073 .stamp.decline'), 'the card is stamped');
  await p.advance(1500);
  assert.equal(p.$('#p-5073'), null, 'and folds away');
});

test('a double click never posts, and the key disarms after 2.4 s', async () => {
  const p = pageIn('station', state());
  await settle();
  const key = p.$('#p-5073 .verdict [data-v="done"]');
  click(key, 1); click(key, 2);
  await p.advance(2500);
  assert.equal(posts(p, '/phones/5073/state').length, 0);
  assert.equal(key.classList.contains('armed'), false);
  assert.equal(key.querySelector('.l').textContent, 'Done');
});

test('the Results count goes up when the spark lands, and once only', async () => {
  const after = without(state({tally: {done: 2, decline: 2, or: 0, auth: 0, failed: 0, all: 4},
    today: [{id: 9, hm: '10:00', v: 'decline', lane: 'gpt', serial: '5073', gmail: 'g', exit: 'PC2'}]}), '5073');
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/state' ? ok({said: 'declined', state: after})
    : c.url === '/station/state' ? {status: 200, body: after} : null});
  await settle();
  primeStream(p);
  const key = p.$('#p-5073 .verdict [data-v="decline"]');
  click(key); await p.advance(400); click(key);
  await settle();
  assert.equal(p.$('#t-decline').textContent, '1', 'not before the spark lands');
  assert.equal(p.$('#t-all').textContent, '3');
  p.finishSparks();
  assert.equal(p.$('#t-decline').textContent, '2');
  assert.equal(p.$('#t-all').textContent, '4');
  await pullNow(p);
  assert.ok(gets(p, '/station/state').length >= 1, 'a pull ran');
  assert.equal(p.$('#t-decline').textContent, '2', 'a pull that already counts it adds nothing');
  assert.equal(p.$('#t-all').textContent, '4');
});

test('a refused verdict puts the card back and says why', async () => {
  const note = 'phone 5073 is already being closed as Done';
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/state' ? {status: 200, body: {ok: false, said: 'no', note, state: state()}} : null});
  await settle();
  const key = p.$('#p-5073 .verdict [data-v="or"]');
  click(key); await p.advance(400); click(key);
  await settle();
  const card = p.$('#p-5073');
  assert.ok(card, 'the card is back');
  assert.equal(card.classList.contains('closing'), false);
  assert.equal(card.querySelector('.stamp'), null);
  assert.equal(toast(p), note);
  assert.ok(p.$('#said').classList.contains('no'));
  await p.advance(2000);
  assert.ok(p.$('#p-5073'), 'and it stays');
  p.finishSparks();
  assert.equal(p.$('#t-or').textContent, '0', 'a refused verdict never counts');
});

test('a refused verdict answered after the card folded brings it back', async () => {
  let open;
  const held = new Promise((r) => { open = r; });
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/state'
      ? {after: held, status: 200, body: {ok: false, said: 'no', note: 'nope', state: state()}} : null});
  await settle();
  const key = p.$('#p-5073 .verdict [data-v="or"]');
  click(key); await p.advance(400); click(key);
  await p.advance(1500);
  assert.equal(p.$('#p-5073'), null, 'folded while the answer is out');
  open(); await settle();
  assert.ok(p.$('#p-5073'), 'back from the answer');
  assert.equal(toast(p), 'nope');
});

test('a stale pull after a verdict does not bring the card back', async () => {
  const still = state();
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/state' ? ok({said: 'queued', pending: true, state: still})
    : c.url === '/station/state' ? {status: 200, body: still} : null});
  await settle();
  primeStream(p);
  const key = p.$('#p-5073 .verdict [data-v="done"]');
  click(key); await p.advance(400); click(key);
  await p.advance(1500);
  assert.equal(p.$('#p-5073'), null);
  await pullNow(p);
  assert.equal(p.$('#p-5073'), null, 'the tombstone holds it off');
});

test('a verdict pressed in the Live tab stamps the card and flies to its count', async () => {
  let pulled = state();
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  pulled = without(state({tally: {done: 3, decline: 1, or: 0, auth: 0, failed: 0, all: 4},
    today: [{id: 12, hm: '10:01', v: 'done', lane: 'gpt', serial: '5073', gmail: 'g', exit: 'PC2'}]}), '5073');
  p.streams[0].emit(101);
  await p.advance(50);
  assert.ok(p.$('#p-5073 .stamp.done'), 'stamped as the Live tab closed it');
  assert.equal(p.$('#t-done').textContent, '2');
  p.finishSparks();
  assert.equal(p.$('#t-done').textContent, '3');
  await p.advance(1500);
  assert.equal(p.$('#p-5073'), null);
});

// ---------------------------------------------------------------- take
test('Take is one POST for a double click, and its answer lands', async () => {
  const next = state({phones: state().phones.concat([phone('5090', {taken_at: T0})]),
    shelves: Object.assign(state().shelves, {gpt: Object.assign({}, state().shelves.gpt, {ready: 2})})});
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/take' ? ok({said: 'took', note: 'Phone 5090 is yours. Press Boot to switch it on.', state: next}) : null});
  await settle();
  const b = p.$('#take-gpt');
  click(b, 1); click(b, 2); click(b, 1);
  await settle();
  const sent = posts(p, '/station/take');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.lane, 'gpt');
  assert.ok(p.$('#p-5090.arrive'), 'the new card rings as it lands');
  assert.ok(p.$('#p-5090.active'));
  assert.ok(p.scrolled.includes(p.$('#p-5090')));
  assert.equal(toast(p), 'Phone 5090 is yours. Press Boot to switch it on.');
  assert.equal(p.$('#take-gpt .c').textContent, '2');
});

test('a new card replaces its ghost only when that ghost leads', async () => {
  const line = {id: 41, position: 1, joined_at: T0};
  const base = state({phones: [phone('5073')],
    shelves: Object.assign(state().shelves, {gpt: Object.assign({}, state().shelves.gpt, {ready: 0, line})}),
    builds: [{id: 9, lane: 'gpt', stage: 'building', eta_at: T0 + 4 * MIN, late: false, typical_min: 6,
              bare: false, called_off: false, reason: '', chips: ['next free Gmail', 'GPT IP']}]});
  let answer = base;
  const p = pageIn('station', base, {answer: (c) => c.url === '/station/state' ? {status: 200, body: answer} : null});
  await settle();
  primeStream(p);
  assert.deepEqual(p.$$('#bench > section').map((c) => c.id), ['p-5073', 'l-gpt', 'w-9']);
  // The built phone's ghost does not lead: it goes, and the card sits before the ghosts.
  answer = Object.assign({}, base, {builds: [],
    phones: base.phones.concat([phone('5095', {wish: 9, arrived: 'build', taken_at: T0})])});
  await pullNow(p);
  assert.deepEqual(p.$$('#bench > section').map((c) => c.id), ['p-5073', 'p-5095', 'l-gpt']);
  await p.advance(100);
  assert.equal(toast(p), 'Your GPT phone 5095 is built. Press Boot to switch it on.');
  // The line's ghost leads: the phone that served it takes its place.
  const sh = Object.assign({}, base.shelves, {gpt: Object.assign({}, base.shelves.gpt, {line: null})});
  answer = Object.assign({}, answer, {shelves: sh,
    phones: answer.phones.concat([phone('5096', {arrived: 'line', taken_at: T0})])});
  const ghost = p.$('#l-gpt');
  await pullNow(p);
  assert.equal(ghost.isConnected, false);
  assert.deepEqual(p.$$('#bench > section').map((c) => c.id), ['p-5073', 'p-5095', 'p-5096']);
  await p.advance(5000);
  assert.equal(toast(p), 'Your GPT phone is here: 5096. Press Boot to switch it on.');
});

test('Take on an empty shelf joins the line, and Leave the line leaves it', async () => {
  const line = {id: 41, position: 2, joined_at: T0};
  const empty = state({shelves: Object.assign(state().shelves,
    {gpt: {ready: 0, building: 2, eta_at: T0 + 2 * MIN, etas: [T0 + 2 * MIN, T0 + 4 * MIN], late: 0, typical_min: 6, line: null}})});
  const inLine = Object.assign({}, empty, {shelves: Object.assign({}, empty.shelves,
    {gpt: Object.assign({}, empty.shelves.gpt, {line})})});
  const p = pageIn('station', empty, {answer: (c) =>
    c.url === '/station/take' ? ok({said: 'in-line', note: 'You are in line for the next GPT phone.', state: inLine})
    : c.url === '/station/line/leave' ? ok({said: 'left', note: 'You left the line for a GPT phone.', state: empty}) : null});
  await settle();
  assert.equal(p.$('#take-gpt .w').textContent, 'Wait for GPT');
  assert.equal(p.$('#take-gpt .sub').textContent, 'next one in 2 min');
  click(p.$('#take-gpt'));
  await settle();
  assert.equal(p.$('#l-gpt .g-t b').textContent, 'You are number 2 in line');
  assert.equal(p.$('#l-gpt [data-eta]').textContent, '4', 'the second place waits for the second build');
  assert.equal(p.$('#take-gpt .sub').textContent, 'number 2 in line');
  assert.equal(toast(p), 'You are in line for the next GPT phone.');
  click(p.$('#l-gpt [data-a="unqueue"]'));
  await p.advance(1000);
  assert.equal(p.$('#l-gpt'), null);
  assert.equal(toast(p), 'You left the line for a GPT phone.');
});

// ---------------------------------------------------------- change IP
test('a Change IP override ends for good once the server moves on', async () => {
  let pulled = state();
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/phones/5073/proxy' ? ok({said: 'queued', req: 900, pending: true, state: state()})
    : c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  click(p.$('#p-5073 [data-a="ip"]'));
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Changing IP');
  assert.equal(toast(p), 'Changing the IP of 5073. It stays ready to boot.');
  await settle();
  const sent = posts(p, '/phones/5073/proxy');
  assert.equal(sent.length, 1);
  assert.deepEqual([sent[0].body.station, sent[0].body.keep_power, sent[0].body.was], ['1', '1', 'off']);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Changing IP', 'the answer still read off');
  pulled = state({phones: [phone('5073', {power: 'changing'}), state().phones[1]]});
  await pullNow(p);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Changing IP');
  pulled = state({phones: [phone('5073', {exit: 'PC8', last: {id: 900, verb: 'change_proxy', ok: true,
    note: '', was: 'PC2', now: 'PC8', started: false}}), state().phones[1]]});
  await pullNow(p);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Ready', 'not Changing IP again');
  assert.equal(p.$('#p-5073 [data-k="exit"]').textContent, 'PC8');
  assert.ok(p.$('#p-5073 [data-k="ip"]').classList.contains('new'), 'the chip lights');
  assert.equal(toast(p), '5073 moved from PC2 to PC8. It stays ready to boot.');
  await pullNow(p);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Ready');
});

// ------------------------------------------------------------ boot
test('Boot on a ready phone posts the real form into its own tab', async () => {
  const p = pageIn('station', state());
  await settle();
  const form = p.$('#p-5073 form.boot');
  assert.equal(form.getAttribute('target'), 'gf-live-5073');
  assert.equal(form.getAttribute('action'), '/phones/5073/boot');
  const ev = fire(form, 'submit');
  assert.equal(ev.defaultPrevented, false, 'the browser posts it, inside the click');
  assert.match(form.querySelector('[name="press"]').value, /^[A-Za-z0-9_-]{8}$/);
  assert.equal(p.$('#p-5073 [data-k="st"]').textContent, 'Booting');
  assert.equal(p.$('#p-5073 .open').disabled, true);
  assert.equal(toast(p), 'Booting 5073. Its screen opens in its own tab.');
  // An on phone's Boot only brings its tab forward.
  const ev2 = fire(p.$('#p-5061 form.boot'), 'submit');
  assert.equal(ev2.defaultPrevented, true);
  assert.deepEqual(p.opened.map((o) => o.name), ['gf-live-5061']);
  assert.equal(p.windows['gf-live-5061'].location.href, '/station/phones/5061');
});

// ------------------------------------------------------------ focus
test('a card the server removes does not take the focus from an open dialog', async () => {
  let pulled = state();
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  click(p.$('#build'));
  await p.advance(50);
  const kind = p.$('.dlg [data-pick="gpt"]');
  assert.equal(p.focused(), kind);
  pulled = without(state(), '5073');
  await pullNow(p);
  await p.advance(1000);
  assert.equal(p.$('#p-5073'), null);
  assert.equal(p.focused(), kind, 'the focus stays in the dialog');
  // A card that held the focus hands it on: to a Take, the last card gone.
  fire(p.doc, 'keydown', {key: 'Escape'});
  p.focus(p.$('#p-5061 [data-a="back"]'));
  pulled = without(pulled, '5061');
  await pullNow(p);
  assert.equal(p.focused(), p.$('#take-gpt'));
});

// ------------------------------------------------------------ profile
test('a pull does not wipe an open profile edit', async () => {
  let pulled = state();
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  click(p.$('.who .me'));
  assert.equal(p.$('#profile').hidden, false);
  click(p.$('#acct [data-edit="name"]'));
  const input = p.$('#acct form[data-form="name"] input');
  assert.equal(p.focused(), input);
  input.value = 'Mina';
  pulled = state({me: Object.assign({}, state().me, {name: 'Sara B', initial: 'S'})});
  await pullNow(p);
  assert.equal(p.$('#acct form[data-form="name"] input'), input, 'the form is untouched');
  assert.equal(input.value, 'Mina');
  assert.equal(p.$('.p-id [data-me="name"]').textContent, 'Sara B');
});

test('the profile shows the server\'s refusal in the hint', async () => {
  const note = 'Your name has a character that cannot be shown.';
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/me/name' ? {status: 200, body: {ok: false, said: 'bad', note, field: 'name'}} : null});
  await settle();
  click(p.$('.who .me'));
  click(p.$('#acct [data-edit="name"]'));
  const form = p.$('#acct form[data-form="name"]');
  form.querySelector('input').value = 'Mi\u202ena';
  fire(form, 'submit');
  await settle();
  assert.equal(posts(p, '/station/me/name')[0].body.name, 'Mi\u202ena');
  const hint = form.querySelector('.hint');
  assert.equal(hint.textContent, note);
  assert.ok(hint.classList.contains('bad'));
});

test('a saved name closes the form, says so and focuses its Change', async () => {
  const me = Object.assign({}, state().me, {name: 'Mina', initial: 'M'});
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/me/name' ? ok({said: 'saved', note: 'Name saved.', me}) : null});
  await settle();
  click(p.$('.who .me'));
  click(p.$('#acct [data-edit="name"]'));
  const form = p.$('#acct form[data-form="name"]');
  form.querySelector('input').value = 'Mina';
  fire(form, 'submit');
  await settle();
  assert.equal(p.$('#acct form'), null);
  assert.equal(p.$('.who [data-me="name"]').textContent, 'Mina');
  assert.equal(p.$('.who [data-me="initial"]').textContent, 'M');
  assert.equal(toast(p), 'Name saved.');
  assert.equal(p.focused(), p.$('#acct [data-edit="name"]'));
});

test('the username is checked in the prototype\'s words before it is sent', async () => {
  const p = pageIn('station', state());
  await settle();
  click(p.$('.who .me'));
  click(p.$('#acct [data-edit="user"]'));
  const form = p.$('#acct form[data-form="user"]');
  assert.equal(form.querySelectorAll('input').length, 1, 'no current password (the lead)');
  form.querySelector('input').value = '_sara';
  fire(form, 'submit');
  assert.equal(form.querySelector('.hint').textContent, 'Start it with a letter or a digit.');
  form.querySelector('input').value = 'Sa';
  fire(form, 'submit');
  assert.equal(form.querySelector('.hint').textContent, '3 to 20 small letters, digits, dots or underscores.');
  await settle();
  assert.equal(posts(p, '/station/me/username').length, 0);
});

test('a network error on the password is not retried', async () => {
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/me/password' ? new Error('offline') : null});
  await settle();
  click(p.$('.who .me'));
  click(p.$('#acct [data-edit="pw"]'));
  const form = p.$('#acct form[data-form="pw"]');
  form.querySelector('[name="a"]').value = 'old-password';
  form.querySelector('[name="b"]').value = 'new-password';
  form.querySelector('[name="c"]').value = 'new-password';
  fire(form, 'submit');
  await p.advance(5000);
  assert.equal(posts(p, '/station/me/password').length, 1);
  assert.equal(form.querySelector('.hint').textContent,
               'The farm did not answer - reload to see whether it changed.');
  assert.equal(form.querySelector('[name="a"]').value, '', 'the fields are cleared');
});

test('a press lost on the network is sent once more with the same press', async () => {
  let n = 0;
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/take'
    ? (n++ === 0 ? new Error('offline') : ok({said: 'took', note: 'ok', state: state()})) : null});
  await settle();
  click(p.$('#take-gpt'));
  await p.advance(2000);
  const sent = posts(p, '/station/take');
  assert.equal(sent.length, 2);
  assert.equal(sent[0].body.press, sent[1].body.press);
});

// ------------------------------------------------------------- notes
test('two notes in one state are both shown', async () => {
  let pulled = state();
  const p = pageIn('station', state({notes: [{id: 'e1', at: T0 - MIN, serial: '1', lane: 'gpt',
    tone: 'gpt', text: 'an old note'}]}), {answer: (c) =>
    c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  assert.equal(toast(p), '', 'a note already there on the first paint is not said');
  pulled = state({notes: [
    {id: 'e3', at: T0, serial: '5061', lane: 'spotify', tone: 'spotify', text: 'the second'},
    {id: 'e2', at: T0, serial: '5073', lane: 'gpt', tone: 'gpt', text: 'the first'},
    {id: 'e1', at: T0 - MIN, serial: '1', lane: 'gpt', tone: 'gpt', text: 'an old note'}]});
  await pullNow(p);
  assert.equal(toast(p), 'the first');
  await p.advance(3000);
  assert.equal(toast(p), 'the second');
});

// ------------------------------------------------------ reload, stream
test('a new build reloads the page only when it is settled', async () => {
  const pulled = state({rev: 'next'});
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state' ? {status: 200, body: pulled} : null});
  await settle();
  primeStream(p);
  click(p.$('#build'));
  await pullNow(p);
  assert.deepEqual(p.went, [], 'not with the dialog open');
  fire(p.doc, 'keydown', {key: 'Escape'});
  await p.advance(1000);
  assert.deepEqual(p.went, ['reload']);
});

test('a closed stream falls back to polling', async () => {
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state' ? {status: 304} : null});
  await settle();
  p.streams[0].readyState = 2;
  await p.advance(10500);
  assert.equal(gets(p, '/station/state').length, 1);
  const q = pageIn('station', state(), {noStream: true, answer: (c) => c.url === '/station/state' ? {status: 304} : null});
  await settle();
  await q.advance(10500);
  assert.equal(gets(q, '/station/state').length, 1, 'no EventSource at all');
  await q.advance(20000);
  assert.equal(gets(q, '/station/state').length, 3);
});

test('a pull asks with the last ETag and a 304 changes nothing', async () => {
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/state'
    ? (c.headers['If-None-Match'] ? {status: 304} : {status: 200, body: state(), headers: {ETag: 'W/"abc"'}}) : null});
  await settle();
  primeStream(p);
  await pullNow(p);
  await pullNow(p);
  const g = gets(p, '/station/state');
  assert.equal(g.length, 2);
  assert.equal(g[1].headers['If-None-Match'], 'W/"abc"');
  assert.ok(p.$('#p-5073'));
});

test('a 401 with go sends the page to /login', async () => {
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/take'
    ? {status: 401, body: {ok: false, said: 'signed-out', note: 'You are signed out.', go: '/login'}} : null});
  await settle();
  click(p.$('#take-gpt'));
  await settle();
  assert.deepEqual(p.went, ['/login']);
});

test('an answer that is not JSON is reported once, and the report never reports itself', async () => {
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/take' ? {status: 502, body: '<html>bad gateway</html>'}
    : c.url === '/clienterror' ? {status: 204, body: ''} : null});
  await settle();
  click(p.$('#take-gpt'));
  await p.advance(800);
  click(p.$('#take-gpt'));
  await p.advance(2000);
  assert.equal(posts(p, '/clienterror').length, 1);
  assert.equal(posts(p, '/station/take').length, 2);
  assert.equal(toast(p), 'Something broke - it is in the server log.');
});

// -------------------------------------------------------------- code
test('the code is RFC 6238\'s, from the base32 key, in the browser', async () => {
  const p = pageIn('station', state());
  const key = Buffer.from('48656c6c6f21deadbeef', 'hex');     // JBSWY3DPEHPK3PXP
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(Math.floor(T0 / 30000)));
  const hm = createHmac('sha1', key).update(msg).digest();
  const o = hm[19] & 15;
  const want = String(((hm[o] & 127) << 24 | hm[o + 1] << 16 | hm[o + 2] << 8 | hm[o + 3]) % 1000000).padStart(6, '0');
  await waitFor(() => p.$('#p-5073 [data-k="code"]').textContent, 'the code');
  assert.equal(p.$('#p-5073 [data-k="code"]').textContent, want.slice(0, 3) + ' ' + want.slice(3));
  const q = pageIn('station', state(), {noSubtle: true});
  await waitFor(() => q.$('#p-5073 [data-k="code"]').textContent, 'the fallback');
  assert.equal(q.$('#p-5073 [data-k="code"]').textContent, '------');
});

test('a failed copy selects the text and says Ctrl+C', async () => {
  const p = pageIn('station', state(), {clipboardFails: true});
  await settle();
  const line = p.$('#p-5073 .step[data-w="pw"]');
  click(line);
  await settle();
  assert.deepEqual(p.clipboard, ['pw-5073']);
  assert.equal(p.selections.length, 1);
  assert.equal(toast(p), 'Selected. Press Ctrl+C to copy.');
  assert.ok(line.classList.contains('just'));
  const q = pageIn('station', state());
  await settle();
  fire(q.$('#p-5073 .step[data-w="gmail"]'), 'keydown', {key: 'Enter'});
  await settle();
  assert.deepEqual(q.clipboard, ['g5073@gmail.com']);
  assert.equal(toast(q), '5073: Gmail copied');
});

test('a bare phone has one quiet line, and no 2FA key means no code', async () => {
  const p = pageIn('station', state({phones: [
    phone('5080', {bare: true, gmail: '', pw: '', totp: ''}), phone('5081', {totp: ''})]}));
  await settle();
  assert.deepEqual(p.$$('#p-5080 .step').map((s) => s.textContent), ['GoogleNo Google account on this phone']);
  assert.deepEqual(p.$$('#p-5081 .step').map((s) => s.getAttribute('data-w')), ['gmail', 'pw']);
});

// ------------------------------------------------------------- build
test('the build dialog refuses in the prototype\'s words and posts the mapped fields', async () => {
  const after = state({builds: [{id: 55, lane: 'spotify', stage: 'queued', eta_at: null, late: false,
    typical_min: 7, bare: true, called_off: false, reason: '', chips: ['bare phone', 'Spotify IP', 'x@y.com']}]});
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/build' ? ok({said: 'asked', state: after}) : null});
  await settle();
  click(p.$('#build'));
  assert.equal(p.$('#scrim').hidden, false);
  assert.equal(p.$('main').hasAttribute('inert'), true);
  click(p.$('.dlg [data-pick="spotify"]'));
  assert.equal(p.$('[data-dlg="eta"]').textContent, 'about 7 minutes');
  click(p.$('[data-row="acct"] [data-m="manual"]'));
  const acct = p.$('#f-acct');
  acct.value = 'x@y.com:pw1';
  fire(acct, 'input');
  assert.ok(p.$('[data-row="acct"] .field').classList.contains('good'));
  fire(p.$('#dlg'), 'submit');
  await settle();
  assert.equal(posts(p, '/station/build').length, 0);
  const say = p.$('[data-row="acct"] .say');
  assert.equal(say.textContent, 'A Spotify account goes on a phone with no Gmail – set Gmail to None.');
  assert.ok(say.classList.contains('bad'));
  click(p.$('[data-row="gmail"] [data-m="none"]'));
  fire(p.$('#dlg'), 'submit');
  await settle();
  const sent = posts(p, '/station/build');
  assert.equal(sent.length, 1);
  const b = sent[0].body;
  assert.deepEqual([b.kind, b.gmail_mode, b.gmail_line, b.acct_mode, b.acct_line, b.ip_mode, b.ip_line],
                   ['spotify', 'none', '', 'manual', 'x@y.com:pw1', 'auto', '']);
  assert.equal(p.$('#scrim').hidden, true);
  assert.equal(p.$('main').hasAttribute('inert'), false);
  assert.ok(p.$('#w-55'));
  assert.equal(p.$('#w-55 .g-t small').textContent, 'With no Google account. It lands here the moment it is ready.');
  assert.equal(toast(p), 'Building a Spotify phone for you – about 7 minutes.');
});

test('arrows on a setting keep the focus on its radio', async () => {
  const p = pageIn('station', state());
  await settle();
  click(p.$('#build'));
  await p.advance(50);
  const auto = p.$('[data-row="gmail"] [data-m="auto"]');
  p.focus(auto);
  fire(auto, 'keydown', {key: 'ArrowRight'});
  await p.advance(100);
  const manual = p.$('[data-row="gmail"] [data-m="manual"]');
  assert.equal(manual.getAttribute('aria-checked'), 'true');
  assert.equal(p.$('[data-row="gmail"] .field').hidden, false);
  assert.equal(p.focused(), manual, 'the radio keeps the focus, not the field');
});

test('a refusal from the server marks the row it names', async () => {
  const p = pageIn('station', state(), {answer: (c) => c.url === '/station/build'
    ? {status: 200, body: {ok: false, said: 'no', note: 'No free GPT IP – type one under Manual.', field: 'ip'}} : null});
  await settle();
  click(p.$('#build'));
  fire(p.$('#dlg'), 'submit');
  await settle();
  assert.equal(p.$('#scrim').hidden, false, 'the dialog stays open');
  assert.equal(p.$('[data-row="ip"] .say').textContent, 'No free GPT IP – type one under Manual.');
  assert.equal(p.$('.dlg .go').disabled, false);
});

test('an empty pool is said in the dialog before anything is sent', async () => {
  const p = pageIn('station', state({build_form: {gmails_left: 0, free_ips: {gpt: 0, spotify: 3, other: 8},
    stopped: true, typical_min: {gpt: 6, spotify: 7, other: 6}}}));
  await settle();
  click(p.$('#build'));
  assert.equal(p.$('[data-row="gmail"] .say').textContent, 'No free Gmail in the pool – type one under Manual, or pick None.');
  assert.equal(p.$('[data-row="ip"] .say').textContent, 'No free GPT IP – type one under Manual.');
  assert.equal(p.$('[data-dlg="stopped"]').hidden, false);
  fire(p.$('#dlg'), 'submit');
  await settle();
  assert.equal(posts(p, '/station/build').length, 0);
});

// ---------------------------------------------------------- ghosts
test('a build that fails on a pull is said once and becomes a card with Dismiss', async () => {
  const b = {id: 70, lane: 'gpt', stage: 'building', eta_at: T0 + 3 * MIN, late: false, typical_min: 6,
    bare: false, called_off: false, reason: '', chips: ['next free Gmail', 'GPT IP']};
  let pulled = state({builds: [b]});
  const p = pageIn('station', pulled, {answer: (c) =>
    c.url === '/station/state' ? {status: 200, body: pulled}
    : c.url === '/wishes/70/dismiss' ? ok({said: 'dismissed', state: state()}) : null});
  await settle();
  primeStream(p);
  assert.equal(p.$('#w-70 [data-eta]').textContent, '3');
  pulled = state({builds: [Object.assign({}, b, {stage: 'failed', reason: 'The Gmail was refused.'})]});
  await pullNow(p);
  await p.advance(100);
  assert.ok(p.$('#w-70.failed'));
  assert.equal(p.$('#w-70 .g-t b').textContent, 'Your GPT phone was not built');
  assert.equal(p.$('#w-70 .g-t small').textContent, 'The Gmail was refused.');
  assert.equal(toast(p), 'Your GPT phone was not built.');
  click(p.$('#w-70 [data-a="dismiss"]'));
  await p.advance(1000);
  assert.equal(p.$('#w-70'), null);
  assert.equal(posts(p, '/wishes/70/dismiss').length, 1);
});

test('calling a build off posts once and says so', async () => {
  const b = {id: 71, lane: 'other', stage: 'queued', eta_at: null, late: false, typical_min: 6,
    bare: true, called_off: false, reason: '', chips: ['bare phone', 'any IP']};
  const p = pageIn('station', state({builds: [b]}), {answer: (c) => c.url === '/station/builds/71/off'
    ? ok({said: 'called-off', state: state({builds: [Object.assign({}, b, {called_off: true})]})}) : null});
  await settle();
  click(p.$('#w-71 [data-a="unbuild"]'));
  await settle();
  assert.equal(posts(p, '/station/builds/71/off').length, 1);
  assert.equal(p.$('#w-71 .g-t b').textContent, 'Calling the build off');
  assert.equal(p.$('#w-71 [data-a="unbuild"]'), null);
  assert.equal(toast(p), 'The build was called off.');
});

test('Give back folds the card at once and posts where it was pressed', async () => {
  const p = pageIn('station', state(), {answer: (c) =>
    c.url === '/station/phones/5073/back' ? ok({said: 'gave-back', state: without(state(), '5073')}) : null});
  await settle();
  click(p.$('#p-5073 [data-a="back"]'));
  assert.equal(toast(p), 'Phone 5073 is back on the GPT shelf.');
  await p.advance(1000);
  assert.equal(p.$('#p-5073'), null);
  assert.equal(posts(p, '/station/phones/5073/back')[0].body.where, 'station');
});

test('the hash from a Live tab lights its card and focuses its Boot', async () => {
  const p = pageIn('station', state(), {hash: '#p-5061'});
  await settle();
  assert.ok(p.$('#p-5061.active'));
  assert.equal(p.focused(), p.$('#p-5061 .open'));
  assert.deepEqual(p.replaced, ['/station']);
});

test('the hour reads back in N minutes in its last quarter, and never gives back by itself', async () => {
  const p = pageIn('station', state({phones: [phone('5073', {idle_since: T0 - 50 * MIN, taken_at: T0 - 55 * MIN})]}));
  await settle();
  assert.equal(p.$('#p-5073 [data-k="age"]').textContent, 'back in 10 min');
  assert.ok(p.$('#p-5073').classList.contains('late'));
  await p.advance(15 * MIN);
  assert.equal(p.$('#p-5073 [data-k="age"]').textContent, 'back in 0 min');
  assert.ok(p.$('#p-5073'), 'the page never gives a phone back on its own clock');
});

test('a user without the Take tick sees the keys and the Takes shut', async () => {
  const p = pageIn('station', state({may: {take: false, ip: false, build: false}}));
  await settle();
  assert.equal(p.$('#take-gpt').disabled, true);
  assert.equal(p.$('#build').hidden, true);
  assert.equal(p.$('#p-5073 [data-a="ip"]').disabled, true);
  assert.ok(p.$$('#p-5073 .verdict button').every((b) => b.disabled));
});

test('an empty station greets by the part of the day', async () => {
  const p = pageIn('station', state({phones: [], me: Object.assign({}, state().me, {daypart: 'evening'})}));
  await settle();
  assert.equal(p.$('#empty').hidden, false);
  assert.equal(p.$('#empty [data-me="hello"]').textContent, 'Good evening, Sara');
});

// ============================================================ the Live tab
function liveState(over = {}) {
  return Object.assign({
    v: 1, rev: 'test', now: T0, serial: '5073', lane: 'gpt', conn: 'off', url: '',
    exit: 'PC2', taken_at: T0 - 10 * MIN, tab_seen: true, bare: false,
    gmail: 'g5073@gmail.com', pw: 'pw-5073', totp: 'JBSWY3DPEHPK3PXP',
    acct: {title: 'ChatGPT account', kind: '', address: 'mina@proton.me', pw: 'apw',
           totp: '', carried: false},
    may: {take: true, ip: true}, last: null, why: '',
    viewer: {w: 360, box_w: 416, box_h: 752, bar: 32}}, over);
}
const WATCH = '/phones/5073/watching';
const LIVE_STATE = '/station/phones/5073/state';
const sideState = (p) => p.$('#lt-side').getAttribute('data-state');

test('the Live tab beats once on load, and a 503 beat is not a release', async () => {
  const p = pageIn('live', liveState(), {answer: (c) => c.url === WATCH ? {status: 503, body: 'store down'} : null});
  await settle();
  const beats = posts(p, WATCH);
  assert.equal(beats.length, 1, 'once, before the first interval');
  assert.deepEqual(beats[0].body, {csrf: 'tok', station: '1'});
  assert.equal(beats[0].init.redirect, 'manual');
  assert.equal(beats[0].init.keepalive, true);
  assert.equal(beats[0].headers['X-GF-Station'], 'live');
  assert.equal(sideState(p), 'off');
  assert.equal(p.$('[data-lt="st"]').textContent, 'Ready');
  assert.equal(p.$('[data-lt-a="boot"]').hidden, false);
  assert.equal(p.$('[data-lt-a="reload"]').disabled, true);
  assert.equal(p.focused(), p.$('#lt-side'));
  await p.advance(15000);
  assert.equal(posts(p, WATCH).length, 2);
  assert.equal(sideState(p), 'off', 'a store that is down is not a release');
});

test('a 410 beat is released, and released is final', async () => {
  const p = pageIn('live', liveState(), {answer: (c) =>
    c.url === WATCH ? {status: 410, body: 'released'}
    : c.url === LIVE_STATE ? {status: 200, body: liveState({conn: 'on', url: 'https://view.test/p?x=1'})} : null});
  await settle();
  assert.equal(sideState(p), 'released');
  assert.equal(p.$('#live').getAttribute('data-power'), 'gone');
  assert.equal(p.$('[data-lt="note"]').textContent, 'released – this phone is no longer yours');
  assert.ok(p.$$('.lt-side .verdict button').every((b) => b.disabled));
  await p.advance(60000);
  assert.equal(sideState(p), 'released', 'nothing brings it back');
  assert.equal(gets(p, LIVE_STATE).length, 0, 'its polls stop');
  assert.equal(posts(p, WATCH).length, 1, 'and its beat');
  p.fireWin('pagehide');
  assert.equal(p.beacons.length, 0, 'a released tab sends no beacon');
});

test('pagehide sends the closing beacon', async () => {
  const p = pageIn('live', liveState());
  await settle();
  p.fireWin('pagehide');
  assert.equal(p.beacons.length, 1);
  assert.equal(p.beacons[0].url, '/phones/5073/closing');
  assert.equal(p.beacons[0].body.type, 'application/x-www-form-urlencoded');
  assert.equal(await p.beacons[0].body.text(), 'csrf=tok&station=1');
});

test('Back to station opens gf-station, sets only its hash when it is open, and never leaves', async () => {
  const p = pageIn('live', liveState());
  await settle();
  click(p.$('[data-lt-a="station"]'));
  assert.deepEqual(p.opened.map((o) => o.name), ['gf-station']);
  assert.equal(p.windows['gf-station'].location.href, '/station#p-5073', 'no Station was open: one opens');
  const open = {location: {href: 'https://farm.test/station', pathname: '/station', hash: ''},
    focused: 0, focus() { this.focused++; }};
  const q = pageIn('live', liveState(), {windows: {'gf-station': open}});
  await settle();
  click(q.$('[data-lt-a="station"]'));
  assert.equal(open.location.hash, 'p-5073');
  assert.equal(open.location.href, 'https://farm.test/station', 'a fragment move, no reload');
  assert.equal(open.focused, 1);
  assert.deepEqual(q.went, [], 'the Live tab stays where it is');
  const root = {location: {href: 'https://farm.test/', pathname: '/', hash: ''}, focus() {}};
  const r = pageIn('live', liveState(), {windows: {'gf-station': root}});
  await settle();
  click(r.$('[data-lt-a="station"]'));
  assert.equal(root.location.hash, 'p-5073', 'an operator\'s Station at / is found too');
});

test('Change IP that ends off shows Boot again', async () => {
  let polled = liveState({conn: 'changing'});
  const p = pageIn('live', liveState(), {answer: (c) =>
    c.url === '/phones/5073/proxy' ? ok({said: 'queued', req: 77, pending: true, live: liveState({conn: 'changing'})})
    : c.url === LIVE_STATE ? {status: 200, body: polled} : null});
  await settle();
  click(p.$('[data-lt-a="ip"]'));
  assert.equal(sideState(p), 'changing');
  await settle();
  const sent = posts(p, '/phones/5073/proxy');
  assert.deepEqual([sent[0].body.station, sent[0].body.keep_power, sent[0].body.was], ['1', '1', 'off']);
  assert.equal(p.$('[data-lt-a="boot"]').hidden, true);
  await p.advance(1600);
  assert.equal(sideState(p), 'changing');
  polled = liveState({conn: 'off', exit: 'PC8', last: {id: 77, verb: 'change_proxy', ok: true, note: '',
    was: 'PC2', now: 'PC8', started: false}});
  await p.advance(1600);
  assert.equal(sideState(p), 'off');
  assert.equal(p.$('[data-lt-a="boot"]').hidden, false);
  assert.equal(p.$('[data-lt="note"]').textContent, '5073 is on PC8 now – press Boot again');
  assert.equal(p.$('[data-lt="exit"]').textContent, 'PC8');
  assert.ok(p.$('[data-lt="ip"]').classList.contains('new'));
  assert.equal(toast(p), '5073 moved from PC2 to PC8. It stays ready to boot.');
});

test('a Boot that fails after being queued says why', async () => {
  const note = 'IranSpoty Cloud has no machine free for 5073 right now - press Boot again in a minute';
  let polled = liveState({conn: 'booting'});
  const p = pageIn('live', liveState(), {answer: (c) =>
    c.url === '/phones/5073/boot' ? ok({said: 'queued', req: 80, pending: true, live: liveState({conn: 'booting'})})
    : c.url === LIVE_STATE ? {status: 200, body: polled} : null});
  await settle();
  click(p.$('[data-lt-a="boot"]'));
  assert.equal(sideState(p), 'booting');
  assert.equal(p.$('[data-lt="note"]').textContent, 'booting 5073 again – it stays yours');
  await settle();
  assert.equal(posts(p, '/phones/5073/boot')[0].body.station, '1');
  assert.equal(p.$('[data-lt="note"]').textContent, 'booting 5073 again – it stays yours', 'the note holds');
  polled = liveState({conn: 'off', last: {id: 80, verb: 'boot_phone', ok: false, note, was: '', now: '',
    started: false}});
  await p.advance(1600);
  assert.equal(sideState(p), 'off');
  assert.equal(p.$('[data-lt="st"]').textContent, 'Ready');
  assert.equal(toast(p), note);
  assert.ok(p.$('#said').classList.contains('no'));
});

test('data-power is on while the screen connects, and the viewer is framed', async () => {
  const p = pageIn('live', liveState({conn: 'on', url: 'https://view.test/p?x=1'}));
  await settle();
  assert.equal(sideState(p), 'connecting');
  assert.equal(p.$('#live').getAttribute('data-power'), 'on');
  const frame = p.$('#lt-view');
  assert.equal(frame.hidden, false);
  assert.equal(frame.getAttribute('src'), 'https://view.test/p?x=1&w=360');
  assert.equal(p.$('#lt-tools').hidden, true);
  assert.equal(p.$('#lt-screen .ph-wait b').textContent, 'Connecting to phone 5073');
  fire(frame, 'load');
  assert.equal(sideState(p), 'on');
  assert.equal(p.$('#lt-screen').hidden, true);
  assert.equal(p.$('[data-lt="note"]').textContent,
    'watching – it stays yours while this tab is open; closing it switches the phone off');
  assert.match(frame.style.transform, /^translateY\(-[\d.]+px\) scale\([\d.]+\)$/);
  // A viewer link that is not https is never framed.
  const q = pageIn('live', liveState({conn: 'on', url: 'http://view.test/p'}));
  await settle();
  assert.equal(q.$('#lt-view').getAttribute('src'), null);
});

test('a phone that goes off blanks the frame and draws the ready screen', async () => {
  let polled = liveState({conn: 'on', url: 'https://view.test/p'});
  const p = pageIn('live', polled, {answer: (c) => c.url === LIVE_STATE ? {status: 200, body: polled} : null});
  await settle();
  fire(p.$('#lt-view'), 'load');
  polled = liveState({conn: 'off'});
  await p.advance(15100);
  assert.equal(p.$('#lt-view').hidden, true);
  assert.equal(p.$('#lt-view').getAttribute('src'), 'about:blank');
  assert.equal(p.$('#lt-screen .ph-wait b').textContent, '5073 is ready to boot');
  assert.equal(p.$('#lt-screen .ph-wait span:not(.pw)').textContent,
    'It is switched off, on PC2. Press Boot again to switch it on.');
});

test('a verdict in the Live tab takes two taps, releases the tab and goes back to the Station', async () => {
  const p = pageIn('live', liveState(), {answer: (c) => c.url === '/phones/5073/state'
    ? ok({said: 'closed', live: liveState({conn: 'released', why: 'closed'})}) : null});
  await settle();
  const key = p.$('.lt-side .verdict [data-v="auth"]');
  click(key);
  await p.advance(400);
  click(key);
  await settle();
  const sent = posts(p, '/phones/5073/state');
  assert.equal(sent.length, 1);
  assert.deepEqual([sent[0].body.state, sent[0].body.sure, sent[0].body.where], ['auth', '1', 'station-live']);
  assert.equal(sideState(p), 'released');
  assert.equal(p.$('[data-lt="note"]').textContent, 'closed as Auth – it is deleted in a moment');
  assert.equal(toast(p), 'Phone 5073 closed as Auth. It is deleted in a moment.');
  assert.deepEqual(p.opened.map((o) => o.name), ['gf-station']);
});

test('the Live tab shows the app account it knows, and copies it', async () => {
  const p = pageIn('live', liveState());
  await settle();
  const heads = p.$$('.lt-h').map((x) => x.textContent);
  assert.deepEqual(heads, ['On this phone', 'ChatGPT account']);
  click(p.$('[data-lt-copy="apw"]'));
  await settle();
  assert.deepEqual(p.clipboard, ['apw']);
  assert.equal(toast(p), '5073: account password copied');
  const q = pageIn('live', liveState({acct: null}));
  await settle();
  assert.deepEqual(q.$$('.lt-h').map((x) => x.textContent), ['On this phone'], 'no account, no section');
});

test('a refused Boot said in the tab\'s address is toasted once and taken off the address', async () => {
  const p = pageIn('live', liveState({arrival: {said: 'no', req: 944,
    note: 'phone 5073 is changing its IP - wait for it'}}));
  await settle();
  assert.equal(toast(p), 'phone 5073 is changing its IP - wait for it');
  assert.deepEqual(p.replaced, ['/station/phones/5073']);
});

test('a tab that opens on a phone no longer theirs shows nothing of it', async () => {
  const p = pageIn('live', liveState({conn: 'released', why: 'released', gmail: undefined, pw: undefined,
    totp: undefined, acct: undefined}));
  await settle();
  assert.equal(sideState(p), 'released');
  assert.equal(p.$$('.lt-side .step').length, 0);
  assert.equal(posts(p, WATCH).length, 0, 'no beat for a phone that is gone');
});
