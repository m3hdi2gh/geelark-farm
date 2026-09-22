// The console's script, run rather than read.
//
//     node --test tests/dash/
//
// Four behaviours to begin with, chosen because each of them shipped
// broken under a green suite: the suite asserted substrings of this
// file's source, and a substring cannot see a call-ordering mistake, a
// variable nothing reads, or a `catch` that re-sends a form.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consoleIn, fire, settle} from './harness.mjs';

const SHEET = `
  <div class="ov" id="poolov" hidden>
    <section class="sheet" data-sheet="gmail">
      <div class="sheetbody">
        <div class="filters">
          <span class="pill" data-group="" aria-pressed="true"><b>2</b></span>
          <input type="search" class="poolfind">
          <span class="tally"></span>
        </div>
        <table class="pooltable"><tbody>
          <tr data-key="gmail:a@x.com" data-group="" data-state="set aside"
              data-find="a@x.com">
            <td>a@x.com</td>
            <td><form method="post" action="/pools/gmail/free">
              <input type="hidden" name="address" value="a@x.com">
              <button data-busy="Freeing…">Free</button></form></td>
          </tr>
          <tr class="none" hidden><td>Nothing matches that.</td></tr>
        </tbody></table>
      </div>
      <dialog class="editor" data-editor="gmail">
        <form method="post" action="/pools/gmail/edit">
          <input type="hidden" name="address" value="">
          <input name="new_address"><input name="password">
          <input name="secret"><select name="state">
            <option value="free">free</option>
            <option value="set aside">set aside</option></select>
          <span data-who></span>
          <p class="editsay" hidden></p>
          <button class="go">Save</button>
        </form>
      </dialog>
    </section>
  </div>`;

// The row's password and key arrive by their own fetch when Edit is
// pressed (2026-09-21); an answer for that door, beside whatever the
// test's own answer is.
const CREDS = {status: 200, body: JSON.stringify({password: 'old', secret: ''})};
function withCreds(answer) {
  return (call) => /\/credentials\?/.test(call.url) ? CREDS : answer(call);
}

async function openEditor(win) {
  const doc = win.document;
  const dlg = doc.querySelector('dialog.editor');
  const tr = doc.querySelector('tr[data-key]');
  // What `openEditor` is given: a button carrying the address.
  const press = doc.createElement('button');
  press.setAttribute('data-edit', 'a@x.com');
  press.setAttribute('data-pool', 'gmail');
  tr.appendChild(press);
  fire(press, 'click');
  await settle();                      // the credentials land
  return dlg;
}

test('a refused Save keeps the editor open, with what was typed in it',
     async () => {
  const win = consoleIn(SHEET, {
    answer: withCreds(() => ({status: 200, url: '/?said=no:41',
                    body: '<main><p class="said no toast">the Gmail '
                        + 'a@x.com is not free</p></main>'})),
  });
  const doc = win.document;
  const dlg = await openEditor(win);
  assert.equal(dlg.open, true, 'the editor opened');
  assert.equal(dlg.querySelector('input[name=password]').value, 'old',
               'filled from its own fetch, not from the row');

  const box = dlg.querySelector('input[name=password]');
  box.value = 'a-correction-I-spent-minutes-on';
  fire(box, 'input');

  fire(dlg.querySelector('form'), 'submit', {submitter: null});
  await settle();

  assert.equal(dlg.open, true, 'a refusal closed the editor');
  assert.equal(box.value, 'a-correction-I-spent-minutes-on',
               'a refusal threw the typing away');
  const said = dlg.querySelector('.editsay');
  assert.equal(said.hidden, false, 'and said nothing about why');
  assert.match(said.textContent, /not free/);
});

test('a click on the dark asks before it throws work away', async () => {
  const win = consoleIn(SHEET, {answer: withCreds(() => null)});
  const dlg = await openEditor(win);
  const box = dlg.querySelector('input[name=password]');
  box.value = 'typed';
  fire(box, 'input');

  // With showModal the backdrop hit-tests to the <dialog> itself.
  fire(dlg, 'click');
  assert.equal(dlg.open, true, 'it closed without asking');
  const ask = dlg.querySelector('.editask');
  assert.ok(ask, 'nothing asked');
  assert.match(ask.textContent, /Throw away the changes to a@x\.com/);
  assert.equal(box.value, 'typed');
});

test('a throw after the write does not send the form a second time',
     async () => {
  // The server took the command and carried it out; the page then
  // failed to read the answer. The old `catch` covered the whole
  // response handler and its recovery is a second, native POST.
  const win = consoleIn(SHEET, {
    answer: () => ({status: 200, url: '/?said=done:9', textThrows: true}),
  });
  const doc = win.document;
  const form = doc.querySelector('form[action="/pools/gmail/free"]');
  let native = 0;
  form.submit = () => { native++; };

  fire(form, 'submit', {submitter: form.querySelector('button')});
  await settle();

  assert.equal(win.__fetches.length, 1, 'the press never reached the server');
  assert.equal(native, 0,
               'the command went through and was sent again by the catch');
  // And it says so, rather than leaving the page looking ordinary.
  assert.ok(win.document.querySelector('.said.toast'),
            'nothing told anybody the answer was lost');
});

test('a press about one row asks for one row, and takes the fragment',
     async () => {
  const win = consoleIn(SHEET, {
    // Exactly what the server sends: a fragment, with no <main>
    // around it. The first version of this test wrapped it in one and
    // so did not notice that `sayIt` only ever looked inside <main>
    // (2026-09-21).
    answer: () => ({status: 200, url: '/?said=done:9',
                    body: '<div class="rowanswer" data-row-kind="gmail">'
                        + '<p class="said toast">a@x.com is back on the '
                        + 'shelf</p><table><tr data-key="gmail:a@x.com" '
                        + 'data-group="" data-state="free"><td>a@x.com</td>'
                        + '</tr></table></div>'}),
  });
  const doc = win.document;
  const form = doc.querySelector('form[action="/pools/gmail/free"]');
  fire(form, 'submit', {submitter: form.querySelector('button')});
  await settle();

  const sent = win.__fetches[0];
  assert.equal(sent.url, '/pools/gmail/free');
  assert.equal(sent.init.headers['X-GF-Row'], 'gmail',
               'the press did not ask for one row');

  const row = doc.querySelector('tr[data-key="gmail:a@x.com"]');
  assert.equal(row.dataset.state, 'free', 'the row was not put back');
  // And the answer's sentence is on the page, lifted out of the flow so
  // it is not painted behind the manager's backdrop.
  const said = doc.querySelector('main .said');
  assert.ok(said, 'the press said nothing');
  assert.ok(said.classList.contains('up'),
            'the banner was left where the backdrop covers it');
});

test('the sheet is fetched once, and asked about with its stamp after',
     async () => {
  // A sheet that is in is not fetched again; a press on its door asks
  // whether it has moved, with the stamp it was drawn from, and a 304
  // leaves it exactly as it stands.
  const win = consoleIn(
    '<div class="ov" id="poolov" hidden></div>'
    + '<button data-pool="gmail">Manage</button>',
    {answer: (call) => (call.init.headers && call.init.headers['If-None-Match'])
      ? {status: 304}
      : {status: 200, headers: {ETag: 'W/"a"'}, body:
         '<section class="sheet" data-sheet="gmail"><div class="sheetbody">'
         + '</div></section>'}});
  const doc = win.document;
  const door = doc.querySelector('[data-pool=gmail]');

  fire(door, 'click');
  fire(door, 'click');                 // a second press while it is in flight
  assert.equal(win.__fetches.length, 1, 'one press, one fetch');
  await settle();

  const sheet = doc.querySelector('.sheet[data-sheet="gmail"]');
  assert.ok(sheet, 'the sheet never arrived');
  assert.equal(sheet.dataset.etag, 'W/"a"', 'the stamp it was drawn from');
  fire(door, 'click');
  await settle();
  assert.equal(win.__fetches.length, 2, 'a look, not a fetch of the sheet');
  assert.equal(win.__fetches[1].init.headers['If-None-Match'], 'W/"a"');
  assert.equal(doc.querySelector('.sheet[data-sheet="gmail"]'), sheet,
               'told 304, the sheet stands');
});

test('an open sheet takes the rows that moved when its stamp has', async () => {
  // The fetched sheet was a snapshot for the life of the tab: keepSheet
  // looked for the fresh copy in the dashboard's response, which stopped
  // carrying it - so a list a person worked in all afternoon never moved.
  const row = (a) => `<tr data-key="gmail:${a}" data-group=""><td>${a}</td></tr>`;
  const sheetWith = (rows) =>
    '<section class="sheet" data-sheet="gmail"><div class="sheetbody">'
    + '<div class="filters"></div><table><tbody>' + rows
    + '<tr class="none" hidden><td>none</td></tr></tbody></table>'
    + '</div></section>';
  const win = consoleIn(
    '<div class="ov" id="poolov" hidden>' + sheetWith(row('a@x.com')) + '</div>'
    + '<button data-pool="gmail">Manage</button>',
    {answer: () => ({status: 200, headers: {ETag: 'W/"b"'},
                     body: sheetWith(row('a@x.com') + row('b@x.com'))})});
  const doc = win.document;
  const sheet = doc.querySelector('.sheet[data-sheet="gmail"]');
  sheet.dataset.etag = 'W/"a"';
  const first = sheet.querySelector('tr[data-key="gmail:a@x.com"]');

  fire(doc.querySelector('[data-pool=gmail]'), 'click');
  await settle();

  assert.equal(win.__fetches[0].init.headers['If-None-Match'], 'W/"a"');
  assert.equal(sheet.querySelectorAll('tr[data-key]').length, 2,
               'the row that arrived was not merged in');
  assert.equal(sheet.querySelector('tr[data-key="gmail:a@x.com"]'), first,
               'the row that did not change was left where it stood');
  assert.equal(sheet.dataset.etag, 'W/"b"');
});

// A page with one server-owned region and a form beside it, which is
// what the dashboard is: the phone table moves on its own, the build
// card is somebody's half-filled work. The swap is driven by a press
// here rather than by the timer, because the timer's own path is what
// `settled()` decides and that is a different test.
function regioned(rows) {
  return `
  <div class="wide">
    <form class="byhand" method="post" action="/phones/build">
      <input name="note" value="">
    </form>
    <form method="post" action="/phones/3801/state" class="press">
      <button>Done</button>
    </form>
    <div data-live="phones"><table id="phones"><tbody>${rows}</tbody></table>
    </div>
  </div>`;
}

const ONE = '<tr data-view="free"><td>3801</td></tr>';
const TWO = ONE + '<tr data-view="free"><td>3802</td></tr>';

function answerPage(body) {
  return '<html><head><meta name="gf-rev" content="test"></head>'
       + '<body><main>' + body + '</main></body></html>';
}

test('a swap replaces the region and leaves the rest where it stands',
     async () => {
  const win = consoleIn(regioned(ONE), {
    answer: () => ({status: 200, url: '/', body: answerPage(regioned(TWO))}),
  });
  const doc = win.document;

  // Somebody has half filled the form beside the region.
  const note = doc.querySelector('input[name=note]');
  note.value = 'half a sentence';
  const form = doc.querySelector('form.byhand');
  const table = doc.querySelector('#phones');

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();

  assert.equal(doc.querySelectorAll('#phones tbody tr').length, 2,
               'the region did not take the news');
  // The two claims that matter: the nodes outside the region are the
  // ones that were there, so nothing had to be put back afterwards.
  assert.equal(doc.querySelector('form.byhand'), form,
               'the form was replaced, and it was nobody\'s to replace');
  assert.equal(note.value, 'half a sentence');
  assert.notEqual(doc.querySelector('#phones'), table,
                  'the region itself is the server\'s; it should be new');
});

test('a page that has gained something falls back to the whole swap',
     async () => {
  // An alert has arrived. A region swap cannot carry that, and must
  // not silently drop it.
  const win = consoleIn(regioned(ONE), {
    answer: () => ({status: 200, url: '/',
                    body: answerPage('<p class="alert">the breaker is up</p>'
                                     + regioned(TWO))}),
  });
  const doc = win.document;

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();

  assert.ok(doc.querySelector('.alert'), 'the new alert was dropped');
  assert.equal(doc.querySelectorAll('#phones tbody tr').length, 2,
               'and the news with it');
});

test('listeners are bound once per node however often init runs', () => {
  const win = consoleIn(
    '<div id="seg" hidden><button data-show="free">Free</button></div>'
    + '<table id="phones"><tbody><tr data-view="free"><td>1</td></tr>'
    + '</tbody></table><span id="tally"></span>');
  const button = win.document.querySelector('#seg button');
  // Marked the first time, and the mark is what stops a second set of
  // listeners when the node survives a swap - which, with regions, it
  // now does.
  assert.equal(button.dataset.bound, '|seg|');
  assert.ok(win.document.querySelector('#phones'));
});

// The dashboard as it is drawn now: every block the server owns is a
// region of its own, and the chips sit outside the phone table's.
function dashboard(rows, status) {
  return `
  <div class="wide">
    <div class="top" data-live="top"><span class="status">${status}</span></div>
    <div class="row"><span id="tally" data-live="tally">?</span>
      <span class="seg" id="seg" hidden>
        <button type="button" data-show="" aria-pressed="true">All</button>
        <button type="button" data-show="free" aria-pressed="false">Free</button>
      </span></div>
    <form class="byhand" method="post" action="/phones/build">
      <input name="note" value="">
    </form>
    <form method="post" action="/phones/3801/state" class="press">
      <button>Done</button>
    </form>
    <div data-live="phones"><table id="phones"><tbody>${rows}
      <tr class="none" id="nohits" hidden><td>Nothing here matches that.</td></tr>
    </tbody></table></div>
  </div>`;
}

test('every block the server owns moves with the swap, not only the table',
     async () => {
  // For a day the table was the only region, and the swap - finding it
  // - returned before touching anything else: the status line stood
  // still from the moment the tab was opened until it was reloaded.
  const win = consoleIn(dashboard(ONE, '3 building'), {
    answer: () => ({status: 200, url: '/',
                    body: answerPage(dashboard(TWO, '2 building'))}),
  });
  const doc = win.document;
  const note = doc.querySelector('input[name=note]');
  note.value = 'half a sentence';
  const form = doc.querySelector('form.byhand');

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();

  assert.equal(doc.querySelector('.status').textContent, '2 building',
               'the status line is the server\'s and did not move');
  assert.equal(doc.querySelectorAll('#phones tbody tr:not(#nohits)').length, 2);
  assert.equal(doc.querySelector('form.byhand'), form, 'the form is nobody\'s to replace');
  assert.equal(note.value, 'half a sentence');
  assert.equal(win.__went.length, 0, 'and nothing reloaded');
});

test('a chip pressed after a swap filters the rows that are there now',
     async () => {
  // The click handler closed over the <tr> nodes of the page as first
  // drawn; after the region swap replaced them a press moved the
  // counter and not the table.
  const MIXED = ONE + '<tr data-view="mine"><td>3802</td></tr>';
  const win = consoleIn(dashboard(ONE, 'quiet'), {
    answer: () => ({status: 200, url: '/', body: answerPage(dashboard(MIXED, 'quiet'))}),
  });
  const doc = win.document;

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();
  const rows = doc.querySelectorAll('#phones tbody tr:not(#nohits)');
  assert.equal(rows.length, 2, 'the swap brought the second row');

  fire(doc.querySelector('#seg button[data-show=free]'), 'click');

  assert.equal(rows[0].hidden, false, 'the free one stays');
  assert.equal(rows[1].hidden, true, 'the one with somebody is filtered out');
  assert.equal(doc.getElementById('tally').textContent, '1 of 2 shown');
});

test('the toast a press left is not part of the page\'s shape', async () => {
  // `sayIt` puts the banner at the top of <main>, where the server never
  // draws one - so for as long as it was up the shapes differed, and the
  // seconds after every press took the whole-of-main path, which threw
  // the toast (and its Undo) away early.
  const win = consoleIn(dashboard(ONE, 'quiet'), {
    answer: () => ({status: 200, url: '/', body: answerPage(dashboard(TWO, 'quiet'))}),
  });
  const doc = win.document;
  const said = doc.createElement('p');
  said.className = 'said toast up';
  said.textContent = 'x@y is back on the shelf';
  const main = doc.querySelector('main');
  main.insertBefore(said, main.firstChild);
  const form = doc.querySelector('form.byhand');

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();

  assert.equal(doc.querySelector('.said'), said, 'the toast was thrown away');
  assert.equal(doc.querySelector('form.byhand'), form,
               'and the whole-of-main path ran under it');
  assert.equal(doc.querySelectorAll('#phones tbody tr:not(#nohits)').length, 2);
});

test('a region that differs only by the marks init left and the press token is not rebuilt',
     async () => {
  // `once` stamps data-bound on every node it binds, and every form is
  // drawn with a fresh one-time `press` token; the server never draws
  // the first and never draws the second twice - so a region with a
  // form in it was never equal to its fresh copy and was rebuilt on
  // every tick, for nothing.
  let draw = 0;
  const strip = (text) => `
    <div class="wide">
      <div class="alerts" data-live="alerts">
        <div class="alert" data-alert="late">${text}<button data-dismiss>x</button>
          <form method="post" action="/service/pause">
            <input type="hidden" name="press" value="tok${++draw}"><button>Pause</button>
          </form></div>
      </div>
      <form method="post" action="/phones/3801/state" class="press"><button>Done</button></form>
      <div data-live="phones"><table id="phones"><tbody>${ONE}</tbody></table></div>
    </div>`;
  const win = consoleIn(strip('the pass is late'), {
    answer: () => ({status: 200, url: '/', body: answerPage(strip('the pass is late'))}),
  });
  const doc = win.document;
  const alert = doc.querySelector('.alert');
  assert.ok(alert.querySelector('[data-dismiss]').dataset.bound, 'init bound it');

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();

  assert.equal(doc.querySelector('.alert'), alert, 'rebuilt for no change');
});


test('a queued answer is looked for again more than once', async () => {
  // One look, two and a half seconds on, was all a queued press got on
  // a page with no stream and no timer; a Free that took ten seconds
  // landed on a page that never looked again.
  const win = consoleIn(regioned(ONE), {
    answer: (call) => ({status: 200,
                        url: /state$/.test(call.url) ? '/?said=queued:71' : '/',
                        body: answerPage(regioned(ONE))}),
  });
  const doc = win.document;

  fire(doc.querySelector('form.press'), 'submit',
       {submitter: doc.querySelector('form.press button')});
  await settle();
  const first = win.__timers.filter((t) => t.fn && t.ms === 2500);
  assert.equal(first.length, 1, 'the first rung is armed');

  // The first look lands: the swap arms the next rung. (`__runTimers`
  // takes every pending timer out of the list as it runs it, so it is
  // called once - a second call would run the rung being looked for.)
  win.__runTimers();
  await settle();
  const second = win.__timers.filter((t) => t.fn && t.ms === 5000);
  assert.ok(second.length >= 1, 'the second rung was not armed');
});


test("the editor holds its boxes until the row's credentials land",
     async () => {
  // They rode in every row of the sheet; now the one row's are fetched
  // when its Edit is pressed, and until they land a Save would write
  // blanks - so the boxes and Save are held.
  const win = consoleIn(SHEET, {answer: (call) =>
    /\/credentials\?/.test(call.url)
      ? {status: 200, body: JSON.stringify({password: 'from-the-row', secret: 'K'})}
      : null});
  const doc = win.document;
  const dlg = doc.querySelector('dialog.editor');
  const press = doc.createElement('button');
  press.setAttribute('data-edit', 'a@x.com');
  press.setAttribute('data-pool', 'gmail');
  doc.querySelector('tr[data-key]').appendChild(press);

  fire(press, 'click');
  const pw = dlg.querySelector('input[name=password]');
  assert.equal(dlg.open, true);
  assert.equal(pw.disabled, true, 'a Save now would write blanks');
  assert.equal(dlg.querySelector('button.go').disabled, true);
  assert.match(win.__fetches[0].url, /\/pools\/gmail\/credentials\?address=a%40x\.com/);

  await settle();
  assert.equal(pw.value, 'from-the-row');
  assert.equal(dlg.querySelector('input[name=secret]').value, 'K');
  assert.equal(pw.disabled, false, 'released once they landed');
  assert.equal(dlg.querySelector('button.go').disabled, false);
});

test("a press on a pool page's own table takes that page's row and pills",
     async () => {
  // The dedicated pool pages' rows carried no key, so every Free, Edit
  // and Remove there was a redirect and a whole page re-read; the
  // answer is now that page's own row, under its fresh pills.
  const win = consoleIn(
    '<div class="pills"><span>Errored<span class="n">3</span></span></div>'
    + '<table><tr data-key="gmail:a@x.com" data-page-view="errored">'
    + '<td>a@x.com</td><td><form method="post" action="/pools/gmail/free">'
    + '<button>Free</button></form></td></tr>'
    + '<tr data-key="gmail:b@x.com" data-page-view="errored"><td>b@x.com</td></tr>'
    + '</table>',
    {answer: () => ({status: 200, url: '/pools/gmail?view=errored&said=done:9',
                     body: '<div class="rowanswer" data-row-kind="gmail" '
                         + 'data-row-view="errored"><p class="said toast">'
                         + 'a@x.com is back on the shelf</p>'
                         + '<div class="pills"><span>Errored<span class="n">2'
                         + '</span></span></div><table></table></div>'})});
  const doc = win.document;
  const form = doc.querySelector('form[action="/pools/gmail/free"]');
  fire(form, 'submit', {submitter: form.querySelector('button')});
  await settle();

  const sent = win.__fetches[0];
  assert.equal(sent.init.headers['X-GF-Row'], 'gmail');
  assert.equal(sent.init.headers['X-GF-View'], 'errored',
               'the page did not say which view drew the row');
  assert.equal(doc.querySelector('tr[data-key="gmail:a@x.com"]'), null,
               'a row that left the view stays on screen');
  assert.ok(doc.querySelector('tr[data-key="gmail:b@x.com"]'),
            'the row beside it went too');
  assert.equal(doc.querySelector('main .pills .n').textContent, '2',
               'the count did not move with the row');
  assert.ok(doc.querySelector('main .said'), 'the press said nothing');
});

test('a redraw waits for a press in flight', async () => {
  // A queued press arms a re-look at 2.5s; a second press made just
  // before it fired had its busy button and dimmed row swapped out
  // from under it, and its answer landed on detached nodes.
  let release;
  const held = new Promise((r) => { release = r; });
  const win = consoleIn(regioned(ONE), {
    answer: (call) => /3801\/state$/.test(call.url)
      ? {status: 200, url: '/?said=queued:71', body: answerPage(regioned(ONE))}
      : /3802\/state$/.test(call.url)
      ? {status: 200, url: '/?said=done:72', body: answerPage(regioned(ONE)),
         after: held}
      : {status: 200, url: '/', body: answerPage(regioned(TWO))},
  });
  const doc = win.document;
  // The first press: queued, so a re-look is armed.
  const first = doc.querySelector('form.press');
  fire(first, 'submit', {submitter: first.querySelector('button')});
  await settle();
  assert.equal(win.__timers.filter((t) => t.fn && t.ms === 2500).length, 1);

  // The second, still in flight when the re-look fires.
  const second = doc.createElement('form');
  second.method = 'post'; second.action = '/phones/3802/state';
  second.className = 'press';
  second.innerHTML = '<button>Done</button>';
  doc.querySelector('.wide').appendChild(second);
  fire(second, 'submit', {submitter: second.querySelector('button')});
  await settle();
  assert.ok(second.classList.contains('busy'), 'the press is in flight');

  win.__runTimers();
  await settle();
  assert.equal(win.__fetches.filter((f) => f.url === '/').length, 0,
               'the page was redrawn under a press in flight');
  assert.ok(doc.contains(second), 'the form was swapped out under the press');

  release();
  await settle(); await settle(); await settle();
  assert.equal(second.classList.contains('busy'), false, 'released after');
});

test("a Save in the editor is answered with its row, not the whole sheet",
     async () => {
  // The editor's form is not inside the row, so its Save never asked
  // for the one-row answer: the sheet was fetched again and the list
  // scrolled back to the top (the operator, 2026-09-22).
  const win = consoleIn(SHEET, {answer: (call) =>
    /\/credentials\?/.test(call.url)
      ? {status: 200, body: JSON.stringify({password: 'p', secret: 'K'})}
      : {status: 200, url: '/?said=done:12',
         body: '<div class="rowanswer" data-row-kind="gmail">'
             + '<p class="said toast">saved</p><table>'
             + '<tr data-key="gmail:a@x.com" data-group="current" '
             + 'data-state="set aside"><td>a@x.com</td></tr></table></div>'}});
  const doc = win.document;
  const dlg = doc.querySelector('dialog.editor');
  const press = doc.createElement('button');
  press.setAttribute('data-edit', 'a@x.com');
  press.setAttribute('data-pool', 'gmail');
  doc.querySelector('tr[data-key]').appendChild(press);
  fire(press, 'click');
  await settle();
  const form = dlg.querySelector('form');
  assert.equal(form.dataset.key, 'gmail:a@x.com', 'the form knows its row');

  fire(form, 'submit', {submitter: dlg.querySelector('button.go')});
  await settle(); await settle();
  const save = win.__fetches.find((f) => /\/pools\/gmail\/edit$/.test(f.url));
  assert.ok(save, 'no Save was sent');
  assert.equal(save.init.headers['X-GF-Row'], 'gmail',
               'the Save did not ask for one row');
  assert.equal(doc.querySelector('tr[data-key="gmail:a@x.com"]').dataset.group,
               'current', 'the row was not put back');
  assert.equal(dlg.open, false, 'the editor stayed open after a Save');
  assert.equal(win.__fetches.filter((f) => /\/sheet/.test(f.url)).length, 0,
               'the whole sheet was fetched again');
});
