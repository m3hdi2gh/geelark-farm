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
              data-password="old" data-secret="" data-find="a@x.com">
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

function openEditor(win) {
  const doc = win.document;
  const dlg = doc.querySelector('dialog.editor');
  const tr = doc.querySelector('tr[data-key]');
  // What `openEditor` is given: a button carrying the address.
  const press = doc.createElement('button');
  press.setAttribute('data-edit', 'a@x.com');
  tr.appendChild(press);
  fire(press, 'click');
  return dlg;
}

test('a refused Save keeps the editor open, with what was typed in it',
     async () => {
  const win = consoleIn(SHEET, {
    answer: () => ({status: 200, url: '/?said=no:41',
                    body: '<main><p class="said no toast">the Gmail '
                        + 'a@x.com is not free</p></main>'}),
  });
  const doc = win.document;
  const dlg = openEditor(win);
  assert.equal(dlg.open, true, 'the editor opened');

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
  const win = consoleIn(SHEET);
  const dlg = openEditor(win);
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

test('the sheet is fetched once, however often the door is pressed',
     async () => {
  const win = consoleIn(
    '<div class="ov" id="poolov" hidden></div>'
    + '<button data-pool="gmail">Manage</button>',
    {answer: () => ({status: 200, body:
      '<section class="sheet" data-sheet="gmail"><div class="sheetbody">'
      + '</div></section>'})});
  const doc = win.document;
  const door = doc.querySelector('[data-pool=gmail]');

  fire(door, 'click');
  fire(door, 'click');                 // a second press while it is in flight
  assert.equal(win.__fetches.length, 1, 'one press, one fetch');
  await settle();

  assert.ok(doc.querySelector('.sheet[data-sheet="gmail"]'),
            'the sheet never arrived');
  fire(door, 'click');
  assert.equal(win.__fetches.length, 1, 'it was fetched again once it was in');
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
