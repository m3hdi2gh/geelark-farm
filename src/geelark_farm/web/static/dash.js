(function(){
  // The one script, and what it is allowed to do: arrange what is
  // already on the page, and send the page's own forms without leaving
  // it. Every request it makes is one a form on the page declared - the
  // same action, the same fields - and the answer is the same HTML the
  // browser would have shown; only <main> is swapped, so the manager
  // stays open and the scroll stays put. Without the script, every form
  // still posts and every page still reloads (2026-09-05). It was the
  // dashboard's alone until 2026-09-14; every page a signed-in person
  // sees carries it now, and everything dashboard-only in it checks for
  // its element first.
  var store = null;
  try { store = window.sessionStorage; } catch (err) {}

  //: The pools whose Free, Save and Remove answer with one row rather
  //: than with the whole page. A kind not here simply gets the redirect.
  var ROW_ANSWERS = {gmail: 1, gpt: 1, spotify: 1, proxy: 1};

  // Bound once per node, whatever `init` does afterwards.
  //
  // `init()` runs after every swap and exactly one of its binding sites
  // had a guard (the pool sheet's, which survives a swap by design).
  // The rest were safe only because the swap threw their nodes away
  // with their listeners - and step 12 stops doing that, so a second
  // set would be a second sift, a second confirm, a second submit
  // (2026-09-21).
  function once(node, what){
    if (!node) return false;
    var had = node.dataset.bound || '';
    if (had.indexOf('|' + what + '|') >= 0) return false;
    node.dataset.bound = had + '|' + what + '|';
    return true;
  }

  // Which of the three views is on, and the rows it is applied to -
  // read off the page each time. Both lived inside `init()`, so the
  // chip's click handler, bound once per button and the buttons being
  // outside the phone region, closed over the `<tr>` nodes of the page
  // as first drawn: after the region swap replaced them a press moved
  // the counter and not the table, and the filter landed by itself
  // whenever the next tick happened to run `init` again - up to thirty
  // seconds later (2026-09-21, found by audit).
  var viewWant = null;
  function sift(){
    if (viewWant === null) viewWant = (store && store.getItem('gf.view')) || '';
    var want = viewWant;
    // Not the "nothing matches" row: it lives in the same tbody and would
    // otherwise count itself as a phone.
    var rows = document.querySelectorAll('#phones tbody tr:not(#nohits)');
    var tally = document.getElementById('tally');
    var none = document.getElementById('nohits');
    var shown = 0;
    rows.forEach(function(tr){
      var hit = !want || tr.dataset.view === want;
      tr.hidden = !hit;
      if (hit) shown++;
    });
    if (none) {
      // What "nothing" means here. It was one fixed sentence about a
      // search, shown after pressing "With me" on a quiet morning -
      // and there is no search on this table (2026-09-07).
      var cell = none.firstElementChild;
      if (cell) cell.textContent =
        want === 'mine'
          ? 'You are not holding any phone - press Free to see what you '
            + 'can take.'
        : want === 'free'
          ? 'Nothing is free right now - the keeper is building.'
          : 'Nothing here matches that.';
      none.hidden = shown > 0;
    }
    if (tally) tally.textContent = want
      ? shown + ' of ' + rows.length + ' shown'
      : rows.length + (rows.length === 1 ? ' phone' : ' phones');
  }

  function init(){
    var seg = document.getElementById('seg');
    if (seg) {
      seg.hidden = false;
      sift();
      seg.querySelectorAll('button').forEach(function(b){
        b.setAttribute('aria-pressed', String(b.dataset.show === viewWant));
        if (!once(b, 'seg')) return;
        b.addEventListener('click', function(){
          viewWant = b.dataset.show;
          if (store) store.setItem('gf.view', viewWant);
          seg.querySelectorAll('button').forEach(function(o){
            o.setAttribute('aria-pressed', String(o === b));
          });
          sift();
        });
      });
    }

    // An alert, put away for this tab. It comes back in a new one: the
    // page is not deciding the problem is gone, the person is deciding
    // they have read it.
    document.querySelectorAll('.alert[data-alert]').forEach(function(el){
      var key = 'gf.alert.' + el.dataset.alert;
      if (store && store.getItem(key)) el.hidden = true;
      var x = el.querySelector('[data-dismiss]');
      if (x && once(x, 'dismiss')) x.addEventListener('click', function(){
        el.hidden = true;
        if (store) store.setItem(key, '1');
      });
    });

    // What a press said: a toast for a few seconds, and gone from the
    // address so a refresh does not say it again.
    dressToast(document.querySelector('.said.toast'));

    // Build one now: two choices. No Gmail means nothing is signed in,
    // so the account box goes off with it. Which app is not a choice any
    // more - every phone carries all three (2026-09-12). "choose..." on
    // the Gmail and the account opens a small dialog - type one, or pick
    // a free one - and what is typed rides in the card's hidden boxes
    // while the choice shows the address (2026-09-10).
    var byhand = document.querySelector('.byhand');
    if (byhand) {
      var gmailPick = byhand.querySelector('select[name="gmail"]');
      var kindPick = byhand.querySelector('select[name="account_kind"]');
      var acctPick = byhand.querySelector('select[name="app_account"]');
      var acctWrap = byhand.querySelector('#acctwrap');
      var dlg = document.getElementById('account-new');
      var free = {};
      try { free = JSON.parse(acctPick.dataset.rows || '{}'); } catch (e) {}
      // A bare phone carries one kind of account and no other, so the
      // rest leave the list rather than sitting in it to be refused.
      var gate = function(){
        var bare = !!gmailPick && gmailPick.value === 'none';
        if (!kindPick) return;
        var lost = false;
        Array.prototype.forEach.call(kindPick.options, function(o){
          // A Spotify account says which phone it wants, and the two
          // answers do not overlap: each kind is offered on the one it
          // belongs on and nowhere else.
          var ok = (bare ? o.dataset.bare : o.dataset.gmail) === '1';
          o.hidden = !ok; o.disabled = !ok;
          if (!ok && o.selected) lost = true;
        });
        if (lost) { kindPick.value = ''; }
        kinded();
      };
      // Which accounts this kind can use, and which boxes the dialog
      // needs: an eco account is an address alone, a Spotify one has no
      // second factor, a standard GPT one has both.
      // Which kind the list in the box was built for, so a redraw that
      // did not change the kind leaves the choice alone: `gate()` runs
      // on every Gmail change and used to rebuild the list each time,
      // throwing away a deliberately chosen account - or a typed one -
      // and silently substituting the first free row (2026-09-19).
      var builtFor = null;
      var kinded = function(){
        var kind = kindPick ? kindPick.value : '';
        if (acctWrap) acctWrap.hidden = !kind;
        if (!acctPick) return;
        acctPick.disabled = !kind;
        if (kind === builtFor) return;
        builtFor = kind;
        var rows = free[kind] || [];
        // Built, never written as markup: an address is somebody's
        // typing and this script must not put typing into HTML.
        while (acctPick.firstChild) acctPick.removeChild(acctPick.firstChild);
        // An option worth nothing, first: Cancel puts the box back to
        // one of its own options, and without this it landed on
        // selectedIndex -1, posted nothing, and the form built a phone
        // with a kind named and no account on it (2026-09-19).
        var none = document.createElement('option');
        none.value = ''; none.textContent = 'none — sign in later';
        acctPick.appendChild(none);
        rows.forEach(function(a){
          var o = document.createElement('option');
          o.value = a; o.textContent = a; acctPick.appendChild(o);
        });
        var other = document.createElement('option');
        other.value = '__new__';
        other.textContent = rows.length ? 'choose…'
                                        : 'choose… (none free)';
        acctPick.appendChild(other);
        acctPick.value = rows.length ? rows[0] : '';
        if (!dlg) return;
        var wants = kind.indexOf('spotify:') === 0
                  ? ['app_address', 'app_password']
                  : (kind === 'chatgpt:eco' ? ['app_address']
                                            : ['app_address', 'app_password',
                                               'app_secret']);
        dlg.querySelectorAll('[data-field]').forEach(function(box){
          var on = wants.indexOf(box.dataset.field) >= 0;
          box.closest('.field').hidden = !on;
          box.value = '';
        });
        // And the hidden boxes it fills, which the card posts: a
        // password typed for a GPT account must not ride along with a
        // Spotify one chosen after it.
        ['app_password', 'app_secret'].forEach(function(name){
          var box = byhand.querySelector('input[name="' + name + '"]');
          if (box) box.value = '';
        });
        // The dialog's own list of free rows follows the kind too.
        var pick = dlg.querySelector('.pick');
        if (pick) {
          while (pick.firstChild) pick.removeChild(pick.firstChild);
          if (!rows.length) {
            var say = document.createElement('div');
            say.className = 'none';
            say.textContent = 'The pool has nothing free of this kind - ' +
                              'type one above.';
            pick.appendChild(say);
          }
          rows.forEach(function(a){
            var l = document.createElement('label');
            var r = document.createElement('input');
            r.type = 'radio'; r.name = 'pick-account-new'; r.value = a;
            l.appendChild(r);
            l.appendChild(document.createTextNode(' ' + a));
            pick.appendChild(l);
          });
        }
      };
      [gmailPick].forEach(function(p){
        if (once(p, 'gate')) p.addEventListener('change', gate);
      });
      if (once(kindPick, 'kind')) kindPick.addEventListener('change', kinded);
      gate();
      byhand.querySelectorAll('select[data-new]').forEach(function(pick){
        if (!once(pick, 'new')) return;
        var was = pick.value === '__new__' ? '' : pick.value;
        pick.addEventListener('change', function(){
          if (pick.value !== '__new__') { was = pick.value; return; }
          // What Cancel goes back to. Read now rather than kept from
          // bind time: the account box is rebuilt whenever the kind
          // changes, and a value remembered before that is an option
          // the box no longer has.
          openNew(pick, was);
        });
        // The box is refilled by `kinded`, which fires no change event.
        pick.addEventListener('focus', function(){
          if (pick.value !== '__new__') was = pick.value;
        });
      });
      // A choice still on "type a new one" has nothing typed yet: open
      // the dialog rather than send the placeholder.
      if (once(byhand, 'stuck')) byhand.addEventListener('submit', function(e){
        var stuck = Array.prototype.filter.call(
          byhand.querySelectorAll('select[data-new]'),
          function(p){ return !p.disabled && !p.closest('[hidden]')
                          && p.value === '__new__'; })[0];
        if (!stuck) return;
        e.preventDefault(); e.stopImmediatePropagation();
        openNew(stuck, '');
      }, true);
    }

    // Search, the seller, and the three chips - current, errored, spent
    // - one sift per sheet.
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      var chips = sheet.querySelectorAll('.filters .pill[data-group]');
      // The second question, where a pool has one: which kind of
      // account. Its own set of chips, sifting with the first.
      var cats = sheet.querySelectorAll('.filters .pill[data-cat]');
      // Read when the sift runs, not when it was bound: a kept sheet
      // (keepSheet) has its rows swapped under it while the listeners
      // stay, and a list taken here once would sift rows that are gone.
      var body = function(){ return sheet.querySelectorAll('tbody tr:not(.none)'); };
      var none = sheet.querySelector('tbody tr.none');
      var tally = sheet.querySelector('.tally');
      var sift = function(){
        var q = (find ? find.value : '').trim().toLowerCase(), shown = 0;
        var who = seller ? seller.value : '';
        var group = '', cat = '', elsewhere = {};
        chips.forEach(function(c){
          if (c.getAttribute('aria-pressed') === 'true') group = c.dataset.group;
        });
        cats.forEach(function(c){
          if (c.getAttribute('aria-pressed') === 'true') cat = c.dataset.cat;
        });
        var rows = body();
        rows.forEach(function(tr){
          // The row's own values, not its text: the text includes the
          // buttons, so "free" - the most natural word to type - kept
          // nearly every row, and "edit" or "remove" kept all of them
          // (the operator, 2026-09-07).
          var near = (!q || (tr.dataset.find || '').indexOf(q) >= 0)
                  && (!who || tr.dataset.seller === who)
                  && (!cat || tr.dataset.cat === cat);
          // A row the press just moved out of this group is kept for one
          // sift, wearing where it went, and let go on the next.
          var went = tr.classList.contains('moved');
          if (went) tr.classList.remove('moved');
          var hit = near && (!group || tr.dataset.group === group || went);
          tr.hidden = !hit;
          if (hit) shown++;
          // A match under another chip is counted, so "nothing" can say
          // where it went: a used address searched for under `current`
          // would otherwise answer "Nothing matches that" (2026-09-08).
          else if (near) elsewhere[tr.dataset.group]
            = (elsewhere[tr.dataset.group] || 0) + 1;
        });
        if (none) {
          none.hidden = shown > 0;
          var where = Object.keys(elsewhere).map(function(g){
            return elsewhere[g] + ' under ' + g;
          });
          none.firstElementChild.textContent = where.length
            ? 'Nothing here matches that - ' + where.join(', ') + '.'
            : 'Nothing matches that.';
        }
        if (tally) tally.textContent = shown === rows.length
          ? shown + (shown === 1 ? ' row' : ' rows')
          : shown + ' of ' + rows.length + ' shown';
        // A door that answers one group belongs under that group: Free
        // all acts on the whole set-aside list, and a press while you
        // are looking at the working exits would be a surprise.
        sheet.querySelectorAll('[data-for-group]').forEach(function(el){
          el.hidden = el.dataset.forGroup !== group;
        });
      };
      // Once per sheet node. `init` runs after every swap, and a sheet
      // the swap kept (keepSheet) would otherwise gain another set of
      // listeners each time - a sift per swap it had lived through.
      if (once(sheet, 'sift')) {
        if (find) find.addEventListener('input', sift);
        if (seller) seller.addEventListener('change', sift);
        [chips, cats].forEach(function(set){
          set.forEach(function(c){
            c.addEventListener('click', function(){
              set.forEach(function(o){
                o.setAttribute('aria-pressed', String(o === c));
              });
              sift();
            });
          });
        });
      }
      sift();
    });

    // The editor, once per dialog node. `init` runs after every swap
    // and a second set of listeners would ask the same question twice.
    document.querySelectorAll('dialog.editor').forEach(function(dlg){
      if (!once(dlg, 'editor')) return;
      dlg.addEventListener('input', function(){
        dlg.dataset.dirty = '1';
        keepDraft(dlg);
      });
      // Escape, which the document's own handler passes to the dialog
      // on purpose. Easy to hit by accident with a form half filled.
      dlg.addEventListener('cancel', function(ev){
        if (!editorDirty(dlg)) return;
        ev.preventDefault();
        askToDrop(dlg);
      });
    });

    // The page refreshes itself while a phone builds. With the script
    // here, that is a quiet swap rather than a reload.
    var meta = document.querySelector('meta[name="gf-refresh"]');
    if (meta) {
      lookAgain((parseInt(meta.getAttribute('content'), 10) || 30) * 1000);
    }
    listen();
  }

  // A banner the server sent, made into the thing a person actually
  // sees: `up` is the only rule that lifts it out of the page and over
  // everything else (.said.toast.up is position:fixed, z-index 60), so a
  // banner that never gets it is drawn as a static block at the top of
  // `main` - and with the pool manager open, that is underneath its
  // backdrop. Perfectly rendered and perfectly invisible.
  //
  // It lived inside `init()`, and `sayIt` runs AFTER `swapRow` has
  // already called `init()` - so every one-row press put its answer on
  // the page undressed. The operator pressed Free, the row was freed in
  // milliseconds, and nothing on the screen said so (2026-09-20).
  // Out here it is called by both, and the ordering cannot matter again.
  function dressToast(said){
    if (!said || said.classList.contains('up')) return;
    said.classList.add('up');
    if (window.history && history.replaceState && /[?&]said=/.test(location.search)) {
      var clean = location.search.replace(/([?&])said=[^&]*&?/, '$1')
        .replace(/[?&]$/, '');
      history.replaceState(null, '', location.pathname + clean + location.hash);
    }
    // One with a button in it - Undo - waits long enough to be pressed.
    var stay = said.querySelector('form') ? 9000 : 3800;
    setTimeout(function(){ said.classList.add('gone'); }, stay);
    setTimeout(function(){ said.remove(); }, stay + 600);
  }

  // Not while somebody is in the middle of something. A refresh that lands
  // while the manager is open, or while a box is being typed in, wipes
  // what they were doing - which read as the page crashing back to the
  // start (the operator, 2026-09-05). It waits, and tries again shortly.
  // How long after the last scroll a redraw waits. Long enough that a
  // flick of the wheel is one gesture, short enough that a page put down
  // is up to date by the time it is looked at again.
  var SCROLL_QUIET = 1200;
  // And the floor between two redraws. The live stream ticks whenever
  // anything the page draws has changed, which while the farm builds is
  // every second or two - honest, and far more often than a person can
  // read (2026-09-14).
  var SWAP_FLOOR = 4000;
  function scrolled(){ scrolled.at = Date.now(); }
  addEventListener('scroll', scrolled, {capture: true, passive: true});
  addEventListener('wheel', scrolled, {capture: true, passive: true});
  addEventListener('touchmove', scrolled, {capture: true, passive: true});

  function mayRedraw(){
    // Anything the keyboard is on inside the page, not just a box to type
    // in: the swap replaces every child of `main`, so a redraw threw the
    // caret back to the top while somebody was tabbing through it.
    // `:focus-visible` is the keyboard test - a button left focused by a
    // mouse click would otherwise stall the refresh for good
    // (2026-09-07).
    //
    // The test below this was written and never made: `typing` was
    // worked out and then nothing read it, so from September the guard
    // this whole comment describes did not exist. Every page but the
    // dashboard swapped under a typing hand on the four-second floor,
    // which is what "the edit page popped out several times" was made
    // of (the operator, 2026-09-20). A substring test cannot see an
    // unused variable; step 5 of that day's list puts a linter on this
    // file so it cannot happen again.
    var live = document.activeElement;
    var main = document.querySelector('main');
    var typing = !!live
      && (['INPUT', 'TEXTAREA', 'SELECT'].indexOf(live.tagName) >= 0
          || (!!main && main.contains(live)
              && live.matches(':focus-visible')));
    if (typing) return false;
    // A hand on the wheel. The place is put back after a swap, but a
    // redraw in the middle of the gesture still stutters under it, and
    // nothing is so urgent that it cannot wait for the scroll to stop.
    if (Date.now() - (scrolled.at || 0) < SCROLL_QUIET) return false;
    // Reading something they chose: a swap drops the selection.
    var picked = window.getSelection && window.getSelection();
    if (picked && !picked.isCollapsed && String(picked).trim().length > 1)
      return false;
    return true;
  }
  // Every one of the tests above is a reason to wait, and a person can
  // leave any of them standing for ever - a selection is not a gesture,
  // it lasts until they click elsewhere. A page that waits for ever
  // still shows a breathing green dot, so it reads as live while it has
  // quietly stopped. The ceiling is what keeps the promise: held while
  // you are busy, never held silently (2026-09-14).
  var HELD_CEILING = 20000;
  // A sheet somebody opened is a working surface, and the news behind it
  // can wait. The rule was written once and lost: it sat below an
  // unconditional `return` in this function, so it never ran - and the
  // manager blinked once per ceiling under the operator's hand
  // (2026-09-17: "it still jumps, much less often"). The drawer is not
  // one of these: it holds no box to type in and a build being watched
  // should move.
  function heldOpen(){
    var o = ov();
    return !!(o && !o.hidden && openKind && openKind !== 'phone');
  }
  // A question waiting for an answer, and a dialog somebody opened.
  // `askFirst` puts its bubble in the page and the editor holds typing
  // nobody has sent, so a swap takes both - and these sat inside
  // `mayRedraw`, under the ceiling, so twenty seconds was all either of
  // them got. They are working surfaces, like an open sheet, and they
  // are held for as long as they are up (2026-09-20).
  // Presses in flight. A queued press arms a re-look at 2.5s; a second
  // press made just before it fired had its busy button, dimmed row and
  // label swapped out from under it by that redraw, and the answer
  // then landed on detached nodes - the operator saw nothing happen
  // (2026-09-21, found by audit). A redraw waits for the answer.
  var pressing = 0;
  function busyHere(){
    return !!(pressing > 0 || document.querySelector('.mini')
              || document.querySelector('dialog[open]'));
  }
  function settled(){
    // Held for as long as it is open, and one refresh the moment it
    // closes (`shut`). The ceiling below does not apply: it is for the
    // gestures a person leaves standing without meaning to, and an open
    // sheet is not one of those - it is where they are working.
    if (heldOpen() || busyHere()) { settled.since = 0; return false; }
    if (mayRedraw()) { settled.since = 0; return true; }
    settled.since = settled.since || Date.now();
    if (Date.now() - settled.since > HELD_CEILING) {
      settled.since = 0;
      return true;
    }
    return false;
  }
  // The farm's own tick. `/live` holds one connection open and sends a
  // number whenever anything a page draws has changed; the timer above
  // stays as the fallback for a browser or a proxy that will not carry
  // it. Opened once for the life of the tab - `init` runs on every swap,
  // and a stream per swap would be a stream per thirty seconds
  // (2026-09-14).
  function listen(){
    if (listen.on || typeof EventSource === 'undefined') return;
    // Which stream, said by the page: the farm's, or the one that also
    // moves for a log line. No meta, no listening - the Boot tab.
    var which = document.querySelector('meta[name="gf-live"]');
    if (!which || !which.content) return;
    listen.on = true;
    var feed = new EventSource(which.content === 'logs' ? '/live?logs=1'
                                                        : '/live');
    feed.onmessage = function(e){
      var now = parseInt(e.data, 10);
      if (!now || now === listen.seen) { listen.seen = now; return; }
      // The first number is where the farm is, not a change: note it and
      // wait for the next one.
      if (listen.seen === undefined) { listen.seen = now; return; }
      listen.seen = now;
      // Soon, but no sooner than the floor since the last redraw.
      var since = Date.now() - (swapMain.at || 0);
      lookAgain(Math.max(250, SWAP_FLOOR - since));
    };
    feed.onerror = function(){
      // EventSource retries on its own; the timer is untouched, so a
      // stream that never comes back costs nothing but the second.
    };
  }

  // Every re-check goes through here, and the soonest one stands. They
  // each used to call `setTimeout` over whatever was pending, so a press
  // that asked to look again in two and a half seconds had its answer
  // thrown away by the next `init` - which arms the thirty-second one -
  // and the row stayed as it was until the operator pressed a second
  // time (2026-09-14: "the first press does nothing").
  function lookAgain(ms){
    var due = Date.now() + ms;
    // False when an earlier look already stands: the caller's look is
    // covered, not lost.
    if (init.timer && init.due && init.due <= due) return false;
    clearTimeout(init.timer);
    init.due = due;
    init.timer = setTimeout(function(){
      init.due = 0;
      reloadWhenSettled();
    }, ms);
    return true;
  }
  function reloadWhenSettled(){
    if (settled()) { reload(); return; }
    lookAgain(5000);
  }
  // Looks that arm each other: the first is taken now, the rest as each
  // swap lands. The soonest pending look still stands (lookAgain), so a
  // page with its own timer is not slowed by this - only a page without
  // one is given the looks it had not.
  function climb(steps){
    init.ladder = steps.slice(1);
    lookAgain(steps[0]);
  }
  function nextRung(){
    // A rung is spent only when it is armed. The swap that carries the
    // queued answer itself comes through here before the first rung has
    // fired, and `lookAgain` declines it - that rung has to stay for
    // the swap the first look brings.
    if (init.ladder && init.ladder.length && lookAgain(init.ladder[0]))
      init.ladder.shift();
  }

  // ------------------------------------------------------ the manager
  var openKind = null, opener = null;
  function ov(){ return document.getElementById('poolov'); }
  function shut(){
    var mini = document.querySelector('.mini'); if (mini) mini.remove();
    document.querySelectorAll('dialog.editor[open]').forEach(closeEditor);
    behind(false);
    var o = ov(); if (!o) return;
    // Every sheet that is showing a preview or a confirm hands its own
    // body back first. Closed mid-preview, the sheet stayed on it: reopen
    // Manage and you got the old preview with no paste box and no list -
    // and `showInSheet` had swapped away the drawer's mount, so the next
    // serial click left the dashboard altogether (2026-09-07).
    o.querySelectorAll('.sheet').forEach(restoreSheet);
    o.hidden = true; openKind = null; drawerHref = null;
    o.classList.remove('right');
    if (opener && document.contains(opener)) opener.focus();
    opener = null;
    o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = true; });
    // Everything the page held back while the sheet was open, now.
    lookAgain(300);
  }
  function behind(off){
    // The dashboard is a sibling emitted before the overlay, so one flag
    // takes the whole of it out of the tab order. Without it, Tab walked
    // from the last row of the sheet onto the buttons under the dark
    // backdrop and Enter pressed whichever it landed on (2026-09-07).
    var page = document.querySelector('.wide');
    if (page) page.inert = !!off;
  }
  // The sheet, fetched the first time its door is pressed and kept in
  // the DOM afterwards - so `keepSheet`, which updates an open one in
  // place across a swap, is untouched. All three used to be rendered
  // shut inside every response, which is 925,488 of a megabyte for the
  // 99 presses in 100 that never happen (2026-09-20).
  var fetching = {};
  function sheetIn(kind, then){
    var o = ov(); if (!o) return;
    if (o.querySelector('.sheet[data-sheet="' + kind + '"]')) { then(); return; }
    if (fetching[kind]) return;                // one press, one fetch
    fetching[kind] = true;
    fetch('/pools/' + kind + '/sheet', {credentials: 'same-origin'})
      .then(function(r){
        if (!answer(r, true)) return null;
        return r.text().then(function(html){
          return {html: html, tag: tagOf(r)};
        });
      })
      .then(function(fetched){
        fetching[kind] = false;
        if (fetched === null) return;
        var made = parse(fetched.html).querySelector('.sheet');
        if (!made) { location.assign('/pools/' + kind); return; }
        // The stamp it was drawn from, sent back on every look while
        // the sheet is open - see refreshSheet.
        made.dataset.etag = fetched.tag;
        o.insertBefore(made, o.firstChild);
        init();
        then(true);
      })
      .catch(function(){
        fetching[kind] = false;
        // The drawer is a convenience over a page that still exists.
        location.assign('/pools/' + kind);
      });
  }

  function tagOf(r){
    try { return (r.headers && r.headers.get && r.headers.get('ETag')) || ''; }
    catch (err) { return ''; }
  }

  // The pool sheets, as opposed to the two the page draws itself.
  var POOL_SHEETS = ['gmail', 'proxy', 'gpt', 'spotify'];
  var refreshing = {};
  // An open sheet, asked for again with the stamp it was drawn from.
  // The server answers 304 until something it is drawn from has moved,
  // and the rows that changed are merged in place (keepSheet) when it
  // has. The fetched sheet used to be a snapshot for the life of the
  // tab: `keepSheet` looked for the fresh copy in the dashboard's
  // response, and the sheets left that response on 2026-09-20 - so a
  // list a person worked in all afternoon never moved (2026-09-21,
  // found by audit).
  function refreshSheet(kind){
    var o = ov(); if (!o) return;
    var mine = o.querySelector('.sheet[data-sheet="' + kind + '"]');
    if (!mine || refreshing[kind]) return;
    refreshing[kind] = true;
    var asking = {};
    if (mine.dataset.etag) asking['If-None-Match'] = mine.dataset.etag;
    fetch('/pools/' + kind + '/sheet',
          {credentials: 'same-origin', headers: asking})
      .then(function(r){
        refreshing[kind] = false;
        if (r.status === 304) return null;
        if (!answer(r, false)) return null;
        var tag = tagOf(r);
        return r.text().then(function(html){
          keepSheet(o, kind, parse(html));
          var now = o.querySelector('.sheet[data-sheet="' + kind + '"]');
          if (now) now.dataset.etag = tag;
        });
      })
      .catch(function(){ refreshing[kind] = false; });
  }

  function show(kind, fresh, justIn){
    var o = ov();
    // Not a bare return: with no overlay on the page the press would
    // do nothing at all and say nothing about it. The pool has a page
    // of its own and always has.
    if (!o) { location.assign('/pools/' + kind); return; }
    if (!o.querySelector('.sheet[data-sheet="' + kind + '"]')) {
      sheetIn(kind, function(now){ show(kind, fresh, now); });
      return;
    }
    o.querySelectorAll('.sheet').forEach(function(el){
      el.hidden = el.dataset.sheet !== kind;
    });
    o.hidden = false; openKind = kind;
    behind(true);
    // Opened by a person onto a sheet fetched some time ago: ask
    // whether it has moved. Not when it has just arrived, and not when
    // a swap is reopening it - the swap's own path asks.
    if (fresh !== false && !justIn && POOL_SHEETS.indexOf(kind) >= 0)
      refreshSheet(kind);
    var open = o.querySelector('.sheet[data-sheet="' + kind + '"]');
    if (!open) return;
    // The paste box, which is what the manager is opened for. It focused
    // the search unless `focusAdd` was passed, and nothing passed it any
    // more - so a pasted line filtered the list instead of entering it.
    // The `/` shortcut still reaches the search (2026-09-07).
    // Only when a person opened it. A swap reopens the sheet behind
    // them, and focusing the paste box there scrolls the body back to
    // the top - undoing the very place `viewBack` is about to restore
    // (2026-09-14).
    if (fresh === false) return;
    var box = open.querySelector('.addbox textarea')
           || open.querySelector('.poolfind');
    if (box) box.focus();
  }

  document.addEventListener('click', function(e){
    var door = e.target.closest('[data-pool]');
    if (door && !door.dataset.edit) {
      opener = door;
      show(door.dataset.pool);
      return;
    }
    // -> phone: choose the phone rather than take the next one. The row's
    // own form still works without this (the next warm phone).
    var choose = e.target.closest('[data-choose]');
    if (choose) {
      var o2 = ov(), sheet2 = o2 && o2.querySelector('.sheet[data-sheet="send"]');
      if (sheet2) {
        e.preventDefault();
        opener = choose;
        sheet2.querySelectorAll('input[name=addresses]').forEach(function(i){
          i.value = choose.dataset.choose;
        });
        var hint = sheet2.querySelector('[data-hint]');
        if (hint) hint.textContent = choose.dataset.choose;
        o2.querySelectorAll('.sheet').forEach(function(el){
          el.hidden = el !== sheet2;
        });
        o2.hidden = false; openKind = 'send'; behind(true);
        return;
      }
    }
    // A serial opens the phone's page in a drawer, here, rather than
    // leaving the dashboard for it.
    var link = e.target.closest(
      '#phones a[href^="/phones/"], .slab a[href^="/phones/"]');
    if (link && !e.ctrlKey && !e.metaKey && link.target !== '_blank') {
      e.preventDefault();
      opener = link;
      openDrawer(link.href);
      return;
    }
    var o = ov();
    // The editor's own Cancel closes the editor. It used to carry the
    // overlay's `data-shut`, so pressing it threw the whole manager away
    // and put the person back on the dashboard - three clicks from where
    // they were (the operator, 2026-09-07).
    var row = e.target.closest('[data-close-edit]');
    if (row) { e.preventDefault(); closeEditor(row.closest('dialog')); return; }
    // A click on the editor's backdrop reaches the dialog itself -
    // which with showModal() is the whole of the screen outside a
    // 560px box. It closed the editor and threw away everything typed,
    // with no question and no way back (the operator, 2026-09-20).
    if (e.target.matches('dialog.editor')) {
      askToDrop(e.target); return;
    }
    if (e.target.closest('[data-shut]') || e.target === o) { shut(); return; }
    var edit = e.target.closest('[data-edit]');
    if (edit) { openEditor(edit); return; }
    // "Back" inside a sheet that is showing a preview or a confirm goes
    // back to the sheet, not to the page.
    var back = e.target.closest(
      '#poolov .sheetbody.shown a[href="/"], '
      + '#poolov .sheetbody.shown a[href^="/phones/"]');
    if (back) { e.preventDefault(); restoreSheet(back.closest('.sheet')); return; }
    var el = e.target.closest('.cp');
    if (!el) return;
    var text = el.textContent.trim();
    var flash = function(){
      el.classList.add('flash');
      setTimeout(function(){ el.classList.remove('flash'); }, 500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(flash, function(){});
      return;
    }
    var box = document.createElement('textarea');
    box.value = text; box.style.position = 'fixed'; box.style.opacity = '0';
    document.body.appendChild(box); box.select();
    try { document.execCommand('copy'); flash(); } catch (err) {}
    box.remove();
  });
  document.addEventListener('keydown', function(e){
    var o = ov();
    // Escape closes the editor first, and the dialog does that itself;
    // without this it closed the editor and the manager in one press.
    if (e.key === 'Escape' && document.querySelector('dialog.editor[open]'))
      return;
    if (e.key === 'Escape' && o && !o.hidden) { shut(); return; }
    var typing = ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(
      (document.activeElement || {}).tagName) >= 0;
    if (e.key === '/' && o && !o.hidden && !typing) {
      var box = o.querySelector('.sheet:not([hidden]) .poolfind');
      if (box) { e.preventDefault(); box.focus(); }
    }
  });

  // The phone's own page, in a drawer. Its forms post like any other
  // and land back on the dashboard, which reopens the drawer refreshed.
  var drawerHref = null;
  // `again` is a swap reopening the drawer somebody is already reading,
  // not a serial they just clicked. Reading a phone's story, the drawer
  // vanished with its backdrop and slid back in every few seconds, at
  // the top, with its folds shut and the focus on the heading - because
  // a swap rebuilds `#poolov` and this was called afresh each time
  // (2026-09-14).
  function openDrawer(href, again){
    var o = ov();
    if (!o) { location.assign(href); return; }
    var sheet = o.querySelector('.sheet[data-sheet="phone"]');
    if (!sheet) { location.assign(href); return; }
    drawerHref = href;
    // Held open across the refetch, so there is no blink and no gap onto
    // the page underneath.
    if (again) {
      o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = el !== sheet; });
      o.classList.add('right');
      o.hidden = false; openKind = 'phone'; behind(true);
    }
    var mark = ++openDrawer.turn;
    fetch(href, {credentials: 'same-origin'})
      .then(function(r){
        // A session that ended while the drawer was open goes to the
        // sign-in page, not into the drawer.
        if (r.redirected && /\/login(\?|$)/.test(r.url)) {
          location.assign(r.url); return null;
        }
        return r.ok ? r.text() : null;
      })
      .then(function(html){
        // A later click already asked for another phone: this answer is
        // last week's news and must not land on top of it.
        if (html === null || mark !== openDrawer.turn) return;
        var doc = parse(html), main = doc.querySelector('main');
        if (!main) { location.assign(href); return; }
        var h2 = main.querySelector('.top h2');
        var acts = main.querySelector('.top .status');
        sheet.querySelector('[data-title]').textContent = h2 ? h2.textContent : '';
        var head = main.querySelector('.top > span');
        var hint = sheet.querySelector('[data-hint]');
        hint.replaceChildren.apply(
          hint, head ? Array.prototype.slice.call(head.childNodes) : []);
        var body = sheet.querySelector('[data-drawer]');
        if (!body) { location.assign(href); return; }
        // Where they were in the story, and which folds they had open.
        var reading = sheet.querySelector('.sheetbody');
        var top = reading ? reading.scrollTop : 0;
        var open = [];
        body.querySelectorAll('details[open]').forEach(function(d){
          open.push((d.querySelector('summary') || {}).textContent || '');
        });
        var nodes = [];
        if (acts) { acts.className = 'acts'; nodes.push(acts); }
        Array.prototype.slice.call(main.children).forEach(function(n){
          // Not the page's alerts. `showInSheet` was taught this and the
          // drawer was not, so the same red banner arrived a second time
          // on top of the one behind it, an alert already dismissed came
          // back, its dismiss button was dead - no init() ran on the copy
          // - and clicking it navigated away and lost the drawer
          // (2026-09-07).
          if (n.matches('script, .alerts, .banner')) return;
          nodes.push(n);
        });
        body.replaceChildren.apply(body, nodes);
        o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = el !== sheet; });
        o.classList.add('right');
        o.hidden = false; openKind = 'phone'; behind(true);
        if (again) {
          body.querySelectorAll('details').forEach(function(d){
            var word = (d.querySelector('summary') || {}).textContent || '';
            if (open.indexOf(word) >= 0) d.open = true;
          });
          if (reading && top) reading.scrollTop = top;
          return;               // and the focus stays where they put it
        }
        // Its own heading, not its first button: a stray Enter after the
        // drawer opened pressed whatever that button was (2026-09-07).
        var head = sheet.querySelector('[data-title]');
        if (head) { head.tabIndex = -1; head.focus(); }
      })
      .catch(function(){
        // A blip while reading is not a reason to throw them off the
        // dashboard: the drawer keeps what it has and the next tick
        // tries again.
        if (!again) location.assign(href);
      });
  }
  openDrawer.turn = 0;

  // A sheet can show a page of its own - the preview of a paste, the
  // "are you sure" of a remove - in place of its list, and come back.
  function restoreSheet(sheet){
    var body = sheet.querySelector('.sheetbody.shown');
    if (body && body._was) body.replaceWith(body._was);
  }
  function showInSheet(sheet, main){
    var was = sheet.querySelector('.sheetbody');
    var sub = document.createElement('div');
    sub.className = 'sheetbody shown';
    Array.prototype.slice.call(main.children).forEach(function(node){
      // Not the page's heading, and not the page's alerts: the breaker
      // banner is about the farm, not about the paste being previewed,
      // and it arrived a second time inside the sheet on top of the one
      // already on the page behind it (the operator, 2026-09-07).
      if (node.matches('.top, script, .alerts, .banner')) return;
      sub.appendChild(node);
    });
    sub._was = was.classList.contains('shown') ? was._was : was;
    was.replaceWith(sub);
    init();
  }

  // ---------------------------------------------------- the requests
  function parse(html){
    return new DOMParser().parseFromString(html, 'text/html');
  }
  // What the manager looks like right now, so a swap can put it back.
  // `swapMain` kept which sheet was open and nothing else, so every
  // press - and the timer, every thirty seconds, unasked - threw the
  // chip back to `all`, emptied the search and lost the scroll. The list
  // moved under the hand using it (the operator, 2026-09-14).
  // Where the page is scrolled, inside and out. A swap replaces every
  // child of `main`, so every scrollport in it is built again at zero -
  // and the dashboard's phone table is one: `.slab>.tscroll` has its own
  // max-height. Reading row ninety, the operator was thrown back to the
  // top about every thirteen seconds, which is how often the farm moved
  // while it was building (2026-09-14).
  //
  // Matched by position, not by id: the same page redrawn has the same
  // boxes in the same order, and giving each one a name would be a name
  // to keep in step with the markup. The manager's own sheets are left
  // to `viewNow`, which knows which sheet is open.
  function boxes(){
    var here = document.querySelector('main');
    if (!here) return [];
    return Array.prototype.filter.call(
      here.querySelectorAll('.tscroll, .queue'), function(el){
        return !el.closest('#poolov');
      });
  }
  function placeNow(){
    return {win: window.scrollY || 0,
            tops: boxes().map(function(el){ return el.scrollTop; })};
  }
  function placeBack(kept){
    if (!kept) return;
    boxes().forEach(function(el, i){
      if (kept.tops[i]) el.scrollTop = kept.tops[i];
    });
    if (kept.win) window.scrollTo(0, kept.win);
  }

  // Anything typed and not yet sent. A swap replaces every child of
  // `main`, and the guard only holds while the box still has the
  // keyboard - click away from the build card to glance at the table and
  // the next tick empties it. Worst of all silently: the Gmail select
  // goes back to "auto", the hidden password and secret boxes are blank,
  // and Build then spends a pool address instead of the one that was
  // typed (2026-09-14).
  //
  // Only what differs from what the server drew, so a page that has been
  // touched by nobody restores nothing.
  function typedNow(){
    var here = document.querySelector('main');
    if (!here) return [];
    var kept = [];
    // Where the caret is, which nothing has ever put back: the values
    // came back and the cursor went to the top of the page, so a swap
    // mid-word read as the page throwing you out (2026-09-20).
    var live = document.activeElement;
    if (live && live.name && here.contains(live)) {
      kept.at = {key: whichField(live)};
      try {
        kept.at.from = live.selectionStart;
        kept.at.to = live.selectionEnd;
      } catch (err) {}                 // a select has no selection
    }
    here.querySelectorAll('input, textarea, select').forEach(function(el){
      if (!el.name || el.type === 'hidden' && !el.value) return;
      if (el.type === 'checkbox' || el.type === 'radio') {
        if (el.checked !== el.defaultChecked)
          kept.push({key: whichField(el), on: el.checked});
        return;
      }
      var was = el.tagName === 'SELECT'
        ? (Array.prototype.filter.call(el.options, function(o){
            return o.defaultSelected; })[0] || {}).value || ''
        : el.defaultValue;
      if (el.value !== was)
        kept.push({key: whichField(el), value: el.value,
                   word: el.tagName === 'SELECT' && el.selectedOptions[0]
                     ? el.selectedOptions[0].textContent : ''});
    });
    return kept;
  }
  function whichField(el){
    var form = el.form;
    // A radio group shares its name with the other radios in it, so the
    // value is part of which field this is - without it, "error picked"
    // was put back onto the `normal` button and the choice was lost on
    // the next tick (2026-09-17).
    var one = el.type === 'radio' || el.type === 'checkbox' ? '=' + el.value : '';
    return (form ? (form.getAttribute('action') || '') : '') + '|' + el.name + one;
  }
  function typedBack(kept){
    if (!kept) return;
    var here = document.querySelector('main');
    if (!here) return;
    var by = {};
    here.querySelectorAll('input, textarea, select').forEach(function(el){
      if (el.name && !(whichField(el) in by)) by[whichField(el)] = el;
    });
    // The caret first, so a handler woken by the values below cannot
    // take the focus off it again.
    if (kept.at && by[kept.at.key]) {
      var box = by[kept.at.key];
      try {
        box.focus({preventScroll: true});
        if (kept.at.from !== undefined && box.setSelectionRange)
          box.setSelectionRange(kept.at.from, kept.at.to);
      } catch (err) {}
    }
    if (!kept.length) return;
    var told = [];
    kept.forEach(function(was){
      var el = by[was.key];
      if (!el) return;
      if ('on' in was) { el.checked = was.on; return; }
      // A value the dialog added to the list is not in the fresh copy of
      // it, so it is put back too - or the select would silently fall to
      // its first option, which is the whole trap.
      if (el.tagName === 'SELECT'
          && !Array.prototype.some.call(el.options, function(o){
               return o.value === was.value; })) {
        var made = document.createElement('option');
        made.value = was.value;
        made.textContent = was.word || was.value;
        el.insertBefore(made, el.firstChild);
      }
      el.value = was.value;
      if (el.tagName === 'SELECT') told.push(el);
    });
    // Setting `.value` fires nothing, so the build card's own gate never
    // heard that the Gmail was back and left the account box hidden and
    // disabled - and Build then posted a kind with no account on it.
    // `openNew` already does exactly this, at its own Cancel, with a
    // comment about the same trap. In a second pass, so a handler that
    // rebuilds a later box cannot undo a value this one has yet to put
    // back.
    told.forEach(function(el){ el.dispatchEvent(new Event('change')); });
  }

  function viewNow(){
    var seen = {};
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var kind = sheet.dataset.sheet;
      var on = sheet.querySelector('.filters .pill[data-group][aria-pressed="true"]');
      var onCat = sheet.querySelector('.filters .pill[data-cat][aria-pressed="true"]');
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      // `.sheetbody` is the scrollport, not `.tscroll`: the CSS gives
      // `.sheetbody>.tscroll` overflow:visible on purpose so the sticky
      // headers work, and an overflow:visible box always reports a
      // scrollTop of zero. So this saved nothing at all, every time
      // (2026-09-14).
      var scroll = sheet.querySelector('.sheetbody')
                || sheet.querySelector('.tscroll');
      seen[kind] = {group: on ? on.dataset.group : null,
                    cat: onCat ? onCat.dataset.cat : null,
                    find: find ? find.value : '',
                    seller: seller ? seller.value : '',
                    top: scroll ? scroll.scrollTop : 0};
    });
    return seen;
  }
  function viewBack(seen){
    if (!seen) return;
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var was = seen[sheet.dataset.sheet];
      if (!was) return;
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      if (find && was.find) find.value = was.find;
      if (seller && was.seller) {
        // A seller that is no longer in the list leaves the box alone.
        var there = Array.prototype.some.call(seller.options, function(o){
          return o.value === was.seller; });
        if (there) seller.value = was.seller;
      }
      // Pressing the chip is how the sift is told: it sets the others
      // false, this one true, and runs. Idempotent on the one already on.
      var chip = was.group === null ? null
        : pickData(sheet, '.filters .pill[data-group]', 'group', was.group);
      var kept = was.cat === null || was.cat === undefined ? null
        : pickData(sheet, '.filters .pill[data-cat]', 'cat', was.cat);
      if (kept) kept.click();
      if (chip) chip.click();
      else if (!kept && find) find.dispatchEvent(new Event('input'));
      var scroll = sheet.querySelector('.sheetbody')
                || sheet.querySelector('.tscroll');
      // After the chip's re-sift, which changes how tall the body is.
      if (scroll && was.top) scroll.scrollTop = was.top;
    });
  }
  // Finding a row or a chip by what it carries, rather than building a
  // selector out of it. A value with a quote or a backslash in it makes
  // a selector that does not parse - and one bad character in this file
  // is a dashboard with no working buttons at all, because the whole
  // script stops at it (the operator, 2026-09-14: "Manage does nothing").
  function pickData(root, within, name, value){
    var all = root.querySelectorAll(within);
    for (var i = 0; i < all.length; i++)
      if (all[i].dataset[name] === value) return all[i];
    return null;
  }

  // The rows a press is about: its own, or - for a door that answers a
  // whole group, like Free all - every row under that group.
  function actOn(form, sheet){
    var own = form.closest('tr');
    if (own) return [own];
    var group = form.dataset.forGroup;
    if (!sheet) return [];
    if (!group) return Array.prototype.slice.call(
      sheet.querySelectorAll('tbody tr:not(.none)'));
    return Array.prototype.filter.call(
      sheet.querySelectorAll('tbody tr:not(.none)'), function(tr){
        return tr.dataset.group === group;
      });
  }

  function swapMain(doc){
    var fresh = doc.querySelector('main'), here = document.querySelector('main');
    if (!fresh || !here) { location.reload(); return; }
    // A newer console than this page's: take it whole, script and all.
    var mine = document.querySelector('meta[name="gf-rev"]');
    var theirs = doc.querySelector('meta[name="gf-rev"]');
    if (mine && theirs && mine.content !== theirs.content) {
      location.reload(); return;
    }
    var kept = openKind, seen = viewNow(), place = placeNow();
    var typed = typedNow();
    // The manager somebody is reading is kept, not rebuilt. The overlay
    // is a child of `main`, so every swap replaced it with a fresh copy
    // and showed that again - and the sheet blinked out and back under
    // the operator's hand each time a builder wrote a row, every few
    // seconds while the farm was busy (the operator, 2026-09-17). Now
    // the nodes under the hand stay and only the rows that moved are
    // swapped in (keepSheet). The drawer fetches its own page and is
    // reopened as before.
    var held = (kept && kept !== 'phone') ? ov() : null;
    var nodes = Array.prototype.slice.call(fresh.childNodes).filter(function(n){
      return !(n.nodeType === 1 && n.matches('script'));
    });
    var mini = document.querySelector('.mini'); if (mini) mini.remove();
    // The regions the server owns, replaced on their own. Everything
    // around them is left exactly as it stands - so the caret, the
    // selection, an open dialog and a scroll offset outside a region
    // are not put back afterwards, they were never taken.
    //
    // Only when the page is otherwise the same page: a region swap
    // cannot carry a card that has appeared or a banner that has gone.
    // `sameBones` is that test, and when it fails the old
    // whole-of-main path runs, with all six routines behind it.
    if (!held && sameBones(here, fresh) && swapRegions(here, fresh)) {
      init();
      typedBack(typed);
      viewBack(seen);
      placeBack(place);
      swapMain.at = Date.now();
      nextRung();
      return;
    }
    if (held) {
      // The overlay never leaves the DOM. Taken out and put back - which
      // is what a whole-of-main replace does - its fade and the sheet's
      // rise play again from the start, and that is the very blink being
      // fixed. So everything around it is replaced, and it stays where
      // it is; the copy the swap brought is read and dropped.
      Array.prototype.slice.call(here.childNodes).forEach(function(n){
        if (n !== held) here.removeChild(n);
      });
      var brought = null;
      nodes.forEach(function(n){
        if (n.nodeType === 1 && n.id === 'poolov') { brought = n; return; }
        here.insertBefore(n, held);
      });
      keepSheet(held, kept, brought);
      // The pool sheets are not in what the swap brought; they are
      // asked for on their own, with the stamp.
      if (POOL_SHEETS.indexOf(kept) >= 0) refreshSheet(kept);
    } else {
      here.replaceChildren.apply(here, nodes);
    }
    init();
    if (kept === 'phone' && drawerHref) openDrawer(drawerHref, true);
    else if (held) behind(true);
    else if (kept && kept !== 'send') show(kept, false);
    typedBack(typed);
    viewBack(seen);
    placeBack(place);
    swapMain.at = Date.now();
    nextRung();
  }

  // Whether the two documents are the same page with different numbers
  // in it, rather than a page that has gained or lost something. Read
  // off the shape alone: the same children in the same order, and the
  // same regions among them. A card appearing, an alert arriving, the
  // build card going away with a permission - any of those and the
  // region swap is the wrong tool and the whole-of-main path runs.
  function sameBones(here, fresh){
    var mine = shape(here), theirs = shape(fresh);
    return mine.length > 0 && mine.join('\u0001') === theirs.join('\u0001');
  }
  function shape(root){
    // Not the toast: `sayIt` puts it at the top of <main>, the server
    // never draws one there, and for as long as it was up the shapes
    // differed - so the seconds after every press, the very ones the
    // region swap exists for, took the whole-of-main path and killed the
    // toast early (2026-09-21, found by audit).
    return Array.prototype.slice.call(root.children)
      .filter(function(n){ return !n.matches('script, .said'); })
      .map(function(n){
        return n.tagName + '.' + (n.className || '') + '#' + (n.id || '')
             + '[' + (n.dataset.live || '') + ']';
      });
  }

  // What a region is compared as: without the two things that differ
  // between any two drawings of the same page. The marks `once` leaves
  // on the nodes it has bound, which the server never draws; and the
  // one-time `press` token every form is drawn with afresh - the same
  // token keepSheet leaves out when it compares rows. With either in,
  // no region that held a form was ever "the same", and every one of
  // them was rebuilt on every tick: the build card under a hand
  // reaching for its select, the foot's breathing dot restarted
  // (2026-09-21, seen on the devserver).
  function comparable(node){
    var copy = node.cloneNode(true);
    copy.removeAttribute('data-bound');
    Array.prototype.forEach.call(copy.querySelectorAll('[data-bound]'),
      function(n){ n.removeAttribute('data-bound'); });
    Array.prototype.forEach.call(copy.querySelectorAll('input[name="press"]'),
      function(n){ n.setAttribute('value', ''); });
    return copy;
  }

  // The send list compared without the address the open sheet filled in,
  // which the fresh copy never carries - or it would be rebuilt every tick.
  function sendSame(mine, fresh){
    if (typeof mine.isEqualNode !== 'function') return false;
    var a = comparable(mine), b = comparable(fresh);
    [a, b].forEach(function(copy){
      Array.prototype.forEach.call(
        copy.querySelectorAll('input[name=addresses]'),
        function(i){ i.setAttribute('value', ''); });
    });
    return a.isEqualNode(b);
  }

  // Each region's new contents in place of its old, and nothing else
  // touched. False when the page has no region yet, which is every page
  // but the dashboard for now.
  function swapRegions(here, fresh){
    var regions = fresh.querySelectorAll('[data-live]');
    if (!regions.length) return false;
    var done = 0;
    Array.prototype.forEach.call(regions, function(bring){
      var mine = pickData(here, '[data-live]', 'live', bring.dataset.live);
      if (!mine) return;
      // Nothing to do when the region has not changed: no reflow, no
      // scrollport rebuilt at zero. `isEqualNode` rather than comparing
      // the two as markup - this file may not build or read markup as
      // a string, and the test that holds it to that reads the file,
      // so the word does not belong in a comment either. Where the
      // answer cannot be trusted the region is replaced, which is what
      // it did before.
      var same = typeof mine.isEqualNode === 'function'
              && comparable(mine).isEqualNode(comparable(bring));
      if (!same)
        mine.replaceChildren.apply(
          mine, Array.prototype.slice.call(bring.childNodes));
      done++;
    });
    return done === regions.length;
  }

  // The overlay the swap found open, still in place, with the news from
  // the copy the swap brought carried over: the closed sheets
  // are taken whole (they open current later), and the open one has its
  // rows matched by key - changed ones replaced, gone ones removed, new
  // ones added before the "nothing matches" line - and the counts on its
  // chips copied. Its paste box, its editor, its scroll and the keyboard
  // are never touched. Rows are compared without the one-time `press`
  // token every render draws afresh, or every row would count as changed.
  function keepSheet(held, kind, fresh){
    if (!fresh) return;
    var mine = held.querySelector('.sheet[data-sheet="' + kind + '"]');
    var theirs = fresh.querySelector('.sheet[data-sheet="' + kind + '"]');
    // The sheets the swap brought: the send list and the phone drawer,
    // which are drawn from the page's own data. The pool sheets are not
    // among them - they are fetched - so one already open stays exactly
    // as it is and is updated row by row below.
    Array.prototype.slice.call(fresh.querySelectorAll('.sheet')).forEach(function(s){
      if (s.dataset.sheet === kind) return;
      var old = held.querySelector('.sheet[data-sheet="' + s.dataset.sheet + '"]');
      if (old) old.replaceWith(s); else held.appendChild(s);
    });
    if (!mine || !theirs) return;
    // The send list, open: the phones that can take an account change
    // under it - one that got an account a minute ago was still offered,
    // and the press was refused (the operator, 2026-09-26). Replaced only
    // when it changed, and the account the sheet was opened for is put
    // back on the new rows, since the click that opened it filled them.
    if (kind === 'send') {
      var list = mine.querySelector('[data-live="send"]');
      var flist = theirs.querySelector('[data-live="send"]');
      if (list && flist && !sendSame(list, flist)) {
        var hint = mine.querySelector('[data-hint]');
        var who = hint ? hint.textContent : '';
        list.replaceChildren.apply(
          list, Array.prototype.slice.call(flist.childNodes));
        list.querySelectorAll('input[name=addresses]').forEach(function(i){
          i.value = who;
        });
      }
      return;
    }
    // A sheet showing a page of its own - the preview of a paste, the
    // "are you sure" of a remove - is not a list to update. Its table
    // is that page's, and the pool's rows were being merged into it:
    // the whole pool appeared under the two rows being previewed (the
    // operator, 2026-09-18). What the swap brings goes to the list
    // waiting behind it instead, so Back, the x and the next Manage
    // come back to a current one rather than to the list as it stood
    // before the paste - which is why a pool somebody had just added
    // five accounts to read as empty until the page was reloaded.
    var shown = mine.querySelector('.sheetbody.shown');
    if (shown) {
      var waiting = theirs.querySelector('.sheetbody');
      if (waiting) shown._was = waiting;
      return;
    }
    var body = mine.querySelector('tbody'), fb = theirs.querySelector('tbody');
    if (body && fb) {
      var same = function(tr){
        return tr.outerHTML.replace(/name="press" value="[^"]*"/g, 'name="press"');
      };
      var have = {};
      body.querySelectorAll('tr[data-key]').forEach(function(tr){
        have[tr.dataset.key] = tr;
      });
      var none = body.querySelector('tr.none'), want = {};
      Array.prototype.slice.call(fb.querySelectorAll('tr[data-key]')).forEach(function(tr){
        want[tr.dataset.key] = true;
        var old = have[tr.dataset.key];
        if (!old) body.insertBefore(tr, none);
        else if (same(old) !== same(tr)) old.replaceWith(tr);
      });
      Object.keys(have).forEach(function(k){ if (!want[k]) have[k].remove(); });
    }
    // Both sets of chips, by position: the fresh sheet is the same
    // pool drawn by the same code, so the nth chip is the nth chip.
    var counts = theirs.querySelectorAll('.filters .pill b');
    mine.querySelectorAll('.filters .pill b').forEach(function(b, i){
      if (counts[i]) b.textContent = counts[i].textContent;
    });
  }

  // 3. One row changed, so one row is replaced - not the page under it.
  // A Test or a Free is about a single exit, and swapping the whole of
  // `main` for it is why the list jumped. The fresh document already
  // holds that row; take it and leave everything else alone.
  // The banner the answer came with, put where the page shows banners.
  // A one-row swap replaced the row and dropped everything else the
  // server had said about it.
  function sayIt(doc){
    // `main .said` when the answer is a page; `.said` when it is the
    // one-row fragment, which has no <main> around it - so the banner
    // was found on every answer but the small one this exists for
    // (2026-09-21, found by driving the real server).
    var said = doc.querySelector('main .said') || doc.querySelector('.said');
    var here = document.querySelector('main');
    if (!said || !here) return;
    var old = here.querySelector('.said');
    if (old) old.replaceWith(said);
    else here.insertBefore(said, here.firstChild);
    // Dressed here rather than left to the next `init()`: this is called
    // after swapRow's, so there is no next one.
    dressToast(said);
  }

  // A dedicated pool page's answer carries its pills, because the press
  // that moved the row moved a count.
  function swapPills(doc){
    var fresh = doc.querySelector('.rowanswer .pills');
    var mine = document.querySelector('main .pills');
    if (fresh && mine) mine.replaceWith(fresh);
  }
  function swapRow(doc, key, gone){
    // Under an open sheet, or in a dedicated pool page's own table.
    var mine = pickData(document, '#poolov tr[data-key], main tr[data-key]',
                        'key', key);
    var theirs = pickData(doc, 'tr[data-key]', 'key', key);
    if (!mine) return false;
    // A one-row answer with no row in it is a row that has left the
    // pool - a Remove, or a Save that renamed it. `gone` is what says
    // an empty answer means that rather than "nothing came back".
    if (!theirs && !gone) return false;
    var sheet = mine.closest('.sheet');
    if (!theirs) mine.remove();
    else mine.replaceWith(theirs);
    // The counts on the chips are now a row out. Counted off the table
    // rather than read from the answer, so they cannot drift.
    if (sheet) {
      var tally = {group: {}, cat: {}}, all = 0;
      sheet.querySelectorAll('tbody tr:not(.none)').forEach(function(tr){
        all++;
        ['group', 'cat'].forEach(function(k){
          var v = tr.dataset[k];
          if (v !== undefined) tally[k][v] = (tally[k][v] || 0) + 1;
        });
      });
      ['group', 'cat'].forEach(function(k){
        sheet.querySelectorAll('.filters .pill[data-' + k + ']').forEach(function(c){
          var n = c.querySelector('b'); if (!n) return;
          n.textContent = c.dataset[k] ? (tally[k][c.dataset[k]] || 0) : all;
        });
      });
    }
    // The press may have moved the row out from under the chip being
    // looked at - Free takes an errored Gmail to `current` - and the
    // sift at the end of `init()` would then hide it on the spot. The
    // row simply vanished, which is what "I could not tell whether it
    // worked" was made of (the operator, 2026-09-20). It stays for one
    // beat instead, wearing where it went, and the next redraw takes it.
    if (theirs && sheet) moved(theirs, sheet);
    init();
    // The floor between two redraws starts here too. Harmless while
    // swapRow only ever matched rows under an open sheet, where
    // `heldOpen` defers everything - and a 250ms redraw storm the
    // moment it reaches a row anywhere else (2026-09-20).
    swapMain.at = Date.now();
    return true;
  }

  // Held back from this sift only, by `sift` itself: the row is marked,
  // not exempted, so the next press or the next tick files it away.
  function moved(tr, sheet){
    if (!sheet) return;
    var chip = sheet.querySelector(
      '.filters .pill[data-group][aria-pressed="true"]');
    var want = chip ? chip.dataset.group : '';
    if (!want || tr.dataset.group === want) return;
    tr.classList.add('moved');
    var cell = tr.querySelector('td');
    if (!cell || cell.querySelector('.movedto')) return;
    var tag = document.createElement('span');
    tag.className = 'movedto';
    tag.textContent = 'now under ' + tr.dataset.group;
    cell.appendChild(tag);
  }
  // What every answer is put through before anything is done with it.
  // There were two of these and they had drifted: `reload` refused a
  // non-OK answer, the submit handler did not - so a 500 from a verb
  // had its error page parsed and installed inside the pool sheet, with
  // no word about what had happened (2026-09-20).
  //
  // `null` means "nothing to do with this"; the caller decides what
  // that is worth.
  function answer(r, say){
    // A session that ended, or a store that is down while the farm
    // keeps building: neither is a reason to swap the dashboard for a
    // sign-in card or an error page nobody asked for (2026-09-14).
    if (r.redirected && /\/login(\?|$)/.test(r.url)) {
      location.assign(r.url); return null;
    }
    if (!r.ok) {
      if (say) toast('That did not go through - the server answered '
                     + r.status + '. Nothing was changed by the page.');
      report('http ' + r.status + ' from ' + r.url, '');
      return null;
    }
    return r;
  }

  function reload(){
    fetch(location.pathname + location.search, {credentials: 'same-origin'})
      .then(function(r){
        if (!answer(r, false)) return null;
        if (r.redirected && !isHere(r.url)) return null;
        return r.text();
      })
      .then(function(html){
        if (html === null) { lookAgain(5000); return; }
        // Asked again, because the answer took a moment to come back and
        // a hand may have arrived on the page meanwhile.
        if (!settled()) { lookAgain(5000); return; }
        swapMain(parse(html));
      })
      .catch(function(){ lookAgain(5000); });
  }
  // The page this is, not the dashboard: on Requests, a Retry answers
  // with Requests, and that is "here".
  function isHere(url){
    try { return new URL(url, location.href).pathname === location.pathname; }
    catch (err) { return false; }
  }

  // The row editor: the sheet's one dialog, filled from the row whose
  // Edit was pressed - password and key in clear, so a wrong one can be
  // seen to be wrong (the operator, 2026-09-08). Only `.value` is set;
  // nothing here is read as markup.
  function openEditor(button){
    var sheet = button.closest('.sheet');
    var dlg = sheet && sheet.querySelector('dialog.editor');
    var tr = button.closest('tr');
    if (!dlg || !tr) return;
    var form = dlg.querySelector('form'), f = form.elements;
    var address = button.dataset.edit;
    // The editor's form is not inside the row it edits, so its Save
    // never asked for the one-row answer: the whole sheet was fetched
    // again and the list scrolled back to the top (the operator,
    // 2026-09-22). The row's key rides on the form instead.
    form.dataset.key = tr.dataset.key || '';
    f.address.value = address;
    f.new_address.value = address;
    // The password and the key are read for this one row when the door
    // is pressed - they no longer ride in every row of the sheet. Until
    // they land the boxes are held, or a Save would write blanks.
    f.password.value = '';
    if (f.secret) f.secret.value = '';
    // The Spotify editor has no key and no tick, and a category instead
    // - opened with the row's own kind, or Save would quietly make every
    // edited row `normal` (2026-09-17).
    if (f.clear_secret) f.clear_secret.checked = false;
    if (f.category) f.category.value = tr.dataset.cat || 'normal';
    if (f.seller) f.seller.value = tr.dataset.sellername || '';
    dlg.querySelector('[data-who]').textContent = address;
    // Status: free, set aside, or the word the row has now. A row a
    // phone is behind shows it greyed - the phone decides that one.
    var state = tr.dataset.state || '';
    var word = state === 'set_aside' ? 'set aside' : state;
    Array.prototype.slice.call(f.state.options).forEach(function(opt){
      if (opt.value !== 'free' && opt.value !== 'set aside') opt.remove();
    });
    if (word && word !== 'free' && word !== 'set aside')
      f.state.add(new Option(word, word));
    f.state.value = word || 'free';
    f.state.disabled = state === 'on a phone';
    f.state.title = f.state.disabled
      ? 'a phone is behind this row - the phone decides' : '';
    dlg.dataset.dirty = '';
    editorQuiet(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
    fillCredentials(dlg, f, sheet.dataset.sheet || button.dataset.pool,
                    address);
  }
  // The one row's password and key, fetched when its Edit is pressed.
  // The boxes and Save are held until they land; a draft is put back
  // only after, or the answer would overwrite what was typed.
  function fillCredentials(dlg, f, kind, address){
    var held = [f.password, f.secret, dlg.querySelector('button.go')]
      .filter(Boolean);
    held.forEach(function(el){ el.disabled = true; });
    var door = '/pools/' + kind + '/credentials?address='
             + encodeURIComponent(address);
    fetch(door, {credentials: 'same-origin'})
      .then(function(r){ return answer(r, true) ? r.json() : null; })
      .then(function(creds){
        if (creds === null) throw new Error('no credentials');
        if (f.address.value !== address) return;    // another row since
        f.password.value = creds.password || '';
        if (f.secret) f.secret.value = creds.secret || '';
        held.forEach(function(el){ el.disabled = false; });
        restoreDraft(dlg, f, address);
      })
      .catch(function(){
        editorSays(dlg, null, 'The password and key could not be read; '
                   + 'saving now would write blanks, so Save is held. '
                   + 'Close and open the row again.', true);
      });
  }
  // What they had typed into this same row and not saved. Put back
  // after everything else, because the status box's options are
  // rebuilt in openEditor and a value set before that would be dropped.
  function restoreDraft(dlg, f, address){
    var draft = drafts[draftKey(dlg, address)];
    if (!draft) return;
    Object.keys(draft).forEach(function(name){
      var el = f[name];
      if (!el || el.disabled) return;
      if (el.type === 'checkbox') { el.checked = draft[name]; return; }
      if (el.tagName === 'SELECT'
          && !Array.prototype.some.call(el.options, function(o){
               return o.value === draft[name]; })) return;
      el.value = draft[name];
    });
    dlg.dataset.dirty = '1';
    // Said out loud: boxes holding something other than what the row
    // holds are a trap if nobody is told which is which.
    editorSays(dlg, null, 'Put back what you had typed and not saved. '
                        + 'Cancel goes back to the row as it is.', false);
  }
  function closeEditor(dlg){
    if (!dlg) return;
    if (dlg.open && typeof dlg.close === 'function') dlg.close();
    else dlg.removeAttribute('open');
  }

  // Anything typed into the editor since it opened. `openEditor` fills
  // every box by `.value`, which fires no input event, so this is true
  // only because a person typed.
  function editorDirty(dlg){ return !!dlg && dlg.dataset.dirty === '1'; }

  // What was typed and not saved, kept for the life of the tab and put
  // back when the same row is opened again. The dialog was a pure
  // refill from the row's data attributes, so whatever closed it took
  // the typing with it and there was no way back to it.
  var drafts = {};
  function draftKey(dlg, address){
    return (dlg.dataset.editor || '') + '|' + address;
  }
  function keepDraft(dlg){
    var form = dlg.querySelector('form');
    if (!form || !form.elements.address) return;
    var who = form.elements.address.value;
    if (!who) return;
    var kept = {};
    Array.prototype.forEach.call(form.elements, function(el){
      if (!el.name || el.type === 'hidden' || el.type === 'submit') return;
      kept[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    });
    drafts[draftKey(dlg, who)] = kept;
  }
  // Closed because the work is done, or because they said throw it away:
  // either way the draft goes with it. Every other way of closing -
  // `shut`, a swap - keeps it, so nothing is lost by accident.
  function dropEditor(dlg){
    if (!dlg) return;
    var form = dlg.querySelector('form');
    if (form && form.elements.address)
      delete drafts[draftKey(dlg, form.elements.address.value)];
    dlg.dataset.dirty = '';
    editorQuiet(dlg);
    closeEditor(dlg);
  }
  function editorQuiet(dlg){
    var slot = dlg.querySelector('.editsay');
    if (slot) { slot.hidden = true; slot.classList.remove('bad'); }
    var asked = dlg.querySelector('.editask'); if (asked) asked.remove();
  }
  function editorSays(dlg, doc, words, bad){
    var slot = dlg.querySelector('.editsay');
    if (!slot) return;
    var said = doc && doc.querySelector('main .said');
    slot.textContent = words || (said ? said.textContent.trim()
                                      : 'That did not go through.');
    slot.classList.toggle('bad', bad !== false);
    slot.hidden = false;
  }
  // Closing on a hand that did not mean it. Nothing typed, nothing to
  // ask about.
  function askToDrop(dlg){
    if (!editorDirty(dlg)) { closeEditor(dlg); return; }
    var form = dlg.querySelector('form');
    var who = form && form.elements.address ? form.elements.address.value : '';
    askInEditor(dlg, 'Throw away the changes to ' + (who || 'this row') + '?',
                'Throw away', function(){ dropEditor(dlg); });
  }
  // Its own confirm, built inside the dialog: a modal dialog is in the
  // top layer and its backdrop takes every click underneath, so
  // `askFirst`'s bubble - which hangs off document.body - would be
  // drawn behind it and could not be pressed.
  function askInEditor(dlg, question, answer, then){
    var old = dlg.querySelector('.editask'); if (old) old.remove();
    var box = document.createElement('div');
    box.className = 'mini editask'; box.setAttribute('role', 'dialog');
    var p = document.createElement('p'); p.textContent = question;
    var row = document.createElement('div'); row.className = 'row';
    var keep = document.createElement('button'); keep.type = 'button';
    keep.className = 'quiet'; keep.textContent = 'Keep editing';
    var yes = document.createElement('button'); yes.type = 'button';
    yes.className = 'quiet bad'; yes.textContent = answer;
    row.append(keep, yes); box.append(p, row);
    dlg.appendChild(box);
    yes.focus();
    keep.addEventListener('click', function(){ box.remove(); });
    yes.addEventListener('click', function(){ box.remove(); then(); });
  }

  // "type a new one" on the build card: the dialog `pick` names, filled
  // in, copied into the card's hidden boxes; the address becomes the
  // choice. Cancel puts the choice back where it was.
  function openNew(pick, was){
    var dlg = document.getElementById(pick.dataset.new);
    var form = pick.closest('form');
    if (!dlg || !form) return;
    var address = dlg.querySelector('[data-field$="_address"]');
    var picked = function(){
      return dlg.querySelector('input[type="radio"]:checked');
    };
    // Typing is choosing to type: the picked row lets go. Picking a row
    // is choosing the pool: the boxes empty, the pool has the rest.
    dlg.oninput = function(ev){
      if (ev.target.type === 'radio') {
        dlg.querySelectorAll('[data-field]').forEach(function(i){ i.value = ''; });
      } else {
        dlg.querySelectorAll('input[type="radio"]')
           .forEach(function(r){ r.checked = false; });
      }
    };
    var done = function(use){
      if (use) {
        var row = picked();
        var addr = row ? row.value : (address ? address.value : '').trim();
        if (!addr) { if (address) address.focus(); return; }
        dlg.querySelectorAll('[data-field]').forEach(function(i){
          if (i === address) return;
          var box = form.querySelector('input[name="' + i.dataset.field + '"]');
          if (box) box.value = row ? '' : i.value;
        });
        var old = pick.querySelector('option[data-typed]');
        if (old) old.remove();
        var opt = new Option(addr + (row ? ' (from the pool)' : ' (new)'),
                             addr, true, true);
        opt.setAttribute('data-typed', '1');
        pick.insertBefore(opt, pick.querySelector('option[value="__new__"]'));
        pick.value = addr;
      } else {
        pick.value = was;
      }
      // Setting .value fires nothing, so the card's own gate never heard
      // that the Gmail had gone back to "none" on Cancel and left the
      // account box live over a bare build (2026-09-12).
      pick.dispatchEvent(new Event('change'));
      closeEditor(dlg);
    };
    dlg.querySelector('[data-use]').onclick = function(){ done(true); };
    dlg.querySelector('[data-cancel]').onclick = function(){ done(false); };
    dlg.addEventListener('cancel', function(ev){
      ev.preventDefault(); done(false);
    }, {once: true});
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
    if (address) address.focus();
  }

  // Remove asks first - here, beside the button, not on a page of its own
  // (the operator, 2026-09-05). Saying yes sends the same form with the
  // server's own "sure" field, so the server needs nothing new.
  function askFirst(form, question, answer){
    var old = document.querySelector('.mini'); if (old) old.remove();
    var box = document.createElement('div');
    box.className = 'mini'; box.setAttribute('role', 'dialog');
    var p = document.createElement('p');
    p.textContent = question;
    var row = document.createElement('div'); row.className = 'row';
    var keep = document.createElement('button'); keep.type = 'button';
    keep.className = 'quiet'; keep.textContent = 'Keep it';
    var yes = document.createElement('button'); yes.type = 'button';
    yes.className = 'quiet bad'; yes.textContent = answer;
    row.append(keep, yes); box.append(p, row);
    // Placed in the window, not on the document, and kept inside it: it
    // used to sit at the row's own place on the page, so a Remove near
    // the foot of a long list asked its question below the fold - the
    // press looked like it had done nothing - and one scroll left the
    // bubble hovering over a different row (2026-09-07).
    document.body.appendChild(box);
    var at = form.getBoundingClientRect();
    var size = box.getBoundingClientRect();
    box.style.top = Math.max(
      8, Math.min(at.bottom + 6, window.innerHeight - size.height - 8)) + 'px';
    box.style.left = Math.max(
      8, Math.min(at.right - size.width, window.innerWidth - size.width - 8))
      + 'px';
    yes.focus();
    // And it lives only as long as what it is pointing at stays still.
    window.addEventListener('scroll', function(){ box.remove(); },
                            {capture: true, once: true});
    keep.addEventListener('click', function(){ box.remove(); });
    yes.addEventListener('click', function(){
      box.remove();
      var sure = document.createElement('input');
      sure.type = 'hidden'; sure.name = 'sure'; sure.value = '1';
      form.appendChild(sure);
      form.requestSubmit();
    });
    // Taken off however the bubble goes, not only when Escape is what
    // took it: a confirm answered with the mouse left its listener on
    // the document for the life of the tab, one per press (2026-09-21).
    function esc(ev){
      if (ev.key !== 'Escape') return;
      box.remove();
    }
    document.addEventListener('keydown', esc);
    var drop = box.remove.bind(box);
    box.remove = function(){
      document.removeEventListener('keydown', esc);
      window.removeEventListener('scroll', box.remove, {capture: true});
      drop();
    };
  }

  // A word on this page, for a few seconds.
  function toast(text){
    var old = document.querySelector('.said.toast'); if (old) old.remove();
    var p = document.createElement('p');
    p.className = 'said toast up'; p.textContent = text;
    document.querySelector('main').appendChild(p);
    setTimeout(function(){ p.classList.add('gone'); }, 5200);
    setTimeout(function(){ p.remove(); }, 5800);
  }

  document.addEventListener('submit', function(e){
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.method || '').toLowerCase() !== 'post') return;
    if (form.target) {
      // Boot opens its own tab and that tab waits for the link. This
      // page says so, or the press looked like nothing (2026-09-08).
      if (/[/]boot$/.test(form.action)) {
        var which = (form.action.split('/phones/')[1] || '').split('/')[0];
        toast('Starting ' + (which || 'the phone') + ' - its screen opens '
          + 'in the new tab as soon as GeeLark hands the link back.');
      }
      return;
    }
    if (!document.querySelector('main').contains(form)) return;
    if (/[/]remove$/.test(form.action)
        && !form.querySelector('input[name=sure]')) {
      e.preventDefault();
      var who = (form.querySelector('input[name=address]') || {}).value
             || (form.querySelector('input[name=name]') || {}).value || 'this row';
      askFirst(form, 'Remove ' + who + ' from the pool? The request keeps '
        + 'the row so it can be put back.', 'Remove');
      return;
    }
    // Done and Failed, which delete the phone: asked beside the button,
    // with the words the server would have put on a page of its own.
    if (form.dataset.ask && !form.querySelector('input[name=sure]')) {
      e.preventDefault();
      askFirst(form, form.dataset.ask, form.dataset.yes || 'Yes');
      return;
    }
    e.preventDefault();
    var data = new FormData(form);
    var pressed = e.submitter;
    if (pressed && pressed.name) data.append(pressed.name, pressed.value);
    var sheet = form.closest('#poolov .sheet');
    var asking = form.closest('dialog.editor');
    if (asking) editorQuiet(asking);
    form.classList.add('busy');
    pressing++;
    // `pointer-events:none` does not stop Enter on a focused submit, so
    // the same press went twice (2026-09-07).
    if (pressed) pressed.disabled = true;
    // What it is doing, and to what. A Test all against sixteen exits is
    // sixteen calls to GeeLark and the button said nothing for all of
    // them, which reads as a hang (the operator, 2026-09-14). The rows
    // it is acting on go dim, so the wait has a shape.
    var wasLabel = null;
    if (pressed && pressed.dataset.busy) {
      wasLabel = pressed.textContent;
      pressed.textContent = pressed.dataset.busy;
    }
    var acting = actOn(form, sheet);
    acting.forEach(function(tr){ tr.classList.add('acting'); });
    var key = form.closest('tr') ? form.closest('tr').dataset.key
              : (form.dataset.key || null);
    var sent = false;
    // A press about one row, answered with that row: ~1KB, and nothing
    // to parse a document out of. The header is what says the script is
    // sending - without it the server redirects, which is what a browser
    // with no script gets and has always got.
    var rowKind = key ? key.split(':')[0] : '';
    var rowView = form.closest('tr') ? form.closest('tr').dataset.pageView : '';
    var asking = {};
    if (rowKind && ROW_ANSWERS[rowKind]
        && /[/](free|edit|remove|refund|offer|test)$/.test(form.action)) {
      asking['X-GF-Row'] = rowKind;
      // A row of a dedicated pool page says which view drew it, and the
      // answer is that page's own row, under fresh pills (2026-09-22).
      if (rowView) asking['X-GF-View'] = rowView;
    }
    // As the browser would send it - urlencoded. FormData on its own goes
    // out multipart, which the server does not read, and every field
    // including the csrf token arrived as nothing: "Stale session"
    // inside the manager on the first real press (2026-09-05).
    fetch(form.action, {method: 'POST', body: new URLSearchParams(data),
                        headers: asking,
                        credentials: 'same-origin', redirect: 'follow'})
      .then(function(r){
        // Past here the command has reached the server and been carried
        // out; anything that goes wrong now is the page's, not the
        // request's.
        sent = true;
        pressing--;
        if (!answer(r, true)) return null;
        return r.text().then(function(html){ return {url: r.url, html: html}; });
      })
      .then(function(got){
        // The lock comes off however this ended. It only came off in the
        // `catch`, so a form that answered *successfully* stayed locked -
        // and `form.busy button` is `pointer-events:none`, so after a
        // preview and a Back the Preview button was dead to a real click
        // while looking perfectly ordinary (the operator, 2026-09-07).
        form.classList.remove('busy');
        if (pressed) pressed.disabled = false;
        if (pressed && wasLabel !== null) pressed.textContent = wasLabel;
        acting.forEach(function(tr){ tr.classList.remove('acting'); });
        if (!got) return;
        var doc = parse(got.html);
        // Queued is not done: the lane carries it out a moment later, so
        // look again shortly and the table shows what happened - a marked
        // phone gone, a taken one wearing its name (2026-09-08).
        // Queued is not done: the lane carries it out a moment later, so
        // what came back does not show it yet. Look again shortly - and
        // do NOT put that row back, because the row in this answer is the
        // row before the press (2026-09-14).
        var waiting = /[?&]said=queued/.test(got.url);
        // One look, two and a half seconds on, was all a queued press
        // got on a page with no stream and no timer - the pool pages,
        // the phone page - so a Free that took ten seconds landed on a
        // page that never looked again (2026-09-21, found by audit). A
        // ladder: each look arms the next, until the lane has had every
        // chance it needs.
        if (waiting) climb([2500, 5000, 10000, 20000]);
        // One row's press, already carried out: put that row back and
        // leave the rest of the page alone.
        //
        // Only when it actually did something. The shortcut was taken
        // for anything that was not `queued`, so a refusal - `said=no`,
        // `refused`, `already`, a 409 from the verb - redrew the row
        // exactly as it was and threw away the sentence that said why.
        // The press looked like it had simply done nothing (2026-09-14).
        //
        // Named the other way round on purpose: the words that mean
        // nothing changed are a short closed list, and the ones that
        // mean something did are added to every time a verb is.
        // Followed by the request number, the next field or the end - a
        // word boundary. It was written as backslash-b, which Python's string rules
        // turned into a backspace character before the browser saw it,
        // so the test matched nothing and every refusal took the row
        // shortcut after all (2026-09-14).
        var nothing = /[?&]said=(queued|no|refused|already|twice|gone|off|none|bad|auto)(?:[:&]|$)/;
        var worked = !nothing.test(got.url);
        // The editor closes when the work went through and stays open
        // with the reason in it when it did not. `closeEditor` used to
        // run unconditionally twenty-nine lines above this, so a Save
        // the verb refused discarded the typing and put its reason on
        // the page behind the backdrop (the operator, 2026-09-20).
        var dlg = form.closest('dialog.editor');
        if (dlg) {
          if (!worked) { editorSays(dlg, doc); return; }
          dropEditor(dlg);
        }
        // A paste that has been added is spent. The sheet comes out of
        // its preview here, before the swap, so the swap updates the
        // list rather than the stash behind it - and the box is
        // emptied, because one still holding what is now in the pool
        // invites the same paste twice (the operator, 2026-09-18). An
        // add that was turned away keeps what was typed: that is the
        // thing to correct.
        var turned = /[?&]said=(no|refused|already|bad|none)(?:[:&]|$)/;
        // Sent: the account is on its way to that phone, so the sheet
        // closes. Left open, the page - which holds still for an open
        // sheet - went on showing the account waiting and the phone
        // warm, and a press the keeper took in two seconds read as
        // nothing at all (the operator, 2026-09-26). Turned away, it
        // stays open with the reason.
        // The banner is put in by hand: with the sheet shut the swap takes
        // the region path, which leaves everything outside the regions
        // alone - the "Queued" banner included.
        if (sheet && sheet.dataset.sheet === 'send' && !turned.test(got.url)) {
          shut();
          swapMain(doc);
          sayIt(doc);
          return;
        }
        if (/[/]add$/.test(form.action) && !turned.test(got.url)) {
          restoreSheet(sheet);
          if (sheet) sheet.querySelectorAll('textarea[name=pasted]')
            .forEach(function(box){ box.value = ''; });
        }
        // The one-row answer: a fragment carrying that row and the
        // banner, and nothing else. It is not `isHere` - it is not a
        // page at all - so it is recognised by what it is.
        if (doc.querySelector('.rowanswer')) {
          if (!swapRow(doc, key, true)) { swapMain(doc); return; }
          swapPills(doc);
          sayIt(doc);
          return;
        }
        if (worked && isHere(got.url) && key && swapRow(doc, key)) {
          sayIt(doc);
          return;
        }
        if (isHere(got.url)) { swapMain(doc); return; }
        // Not the dashboard: a preview, a confirm, a refusal. Inside the
        // sheet it came from, if it came from one; else in place of the
        // page, which is what the browser would have done.
        var main = doc.querySelector('main');
        // A press inside the drawer answers with the phone's own page,
        // and `showInSheet` drops `.top` - which is where that page keeps
        // its buttons. So one press emptied the drawer of every control
        // it had, with no toast and no way back but the × (2026-09-07).
        if (sheet && sheet.dataset.sheet === 'phone'
            && got.url.indexOf('/phones/') >= 0) {
          openDrawer(got.url); return;
        }
        if (sheet && main) showInSheet(sheet, main);
        else swapMain(doc);
      })
      .catch(function(err){
        if (!sent) pressing--;
        form.classList.remove('busy');
        if (pressed) pressed.disabled = false;
        acting.forEach(function(tr){ tr.classList.remove('acting'); });
        // Only the request failing is a reason to send the form again.
        // This `catch` used to cover the whole of the handler above as
        // well, and its recovery is a second, native POST - so a throw
        // in `swapMain` AFTER the write had already gone through sent
        // the same command twice and navigated the page away from
        // whatever it was doing (2026-09-20).
        if (sent) {
          report(err && err.message ? err.message : String(err),
                 err && err.stack ? err.stack : '');
          toast('The press went through; the page could not draw the '
                + 'answer. Reload to see where things stand.');
          return;
        }
        form.submit();
      });
  });

  // Nothing in the console has ever been able to say that it broke.
  // A throw in here leaves the page looking perfectly ordinary with
  // half its buttons dead, and the only way anybody has ever found out
  // is the operator saying so (2026-09-20). One POST, into the log
  // table the console already has a page for.
  var told = 0;
  function report(message, stack){
    if (told >= 5 || !message) return;      // one fault, not a flood
    told++;
    try {
      var rev = document.querySelector('meta[name="gf-rev"]');
      var token = document.querySelector('input[name="csrf"]');
      if (!token) return;
      fetch('/clienterror', {
        method: 'POST', credentials: 'same-origin',
        body: new URLSearchParams({
          csrf: token.value,
          message: String(message).slice(0, 500),
          stack: String(stack || '').slice(0, 2000),
          where: location.pathname + location.search,
          rev: rev ? rev.content : ''})});
    } catch (err) {}
  }
  addEventListener('error', function(e){
    report(e.message || 'script error',
           e.error && e.error.stack ? e.error.stack
                                    : (e.filename || '') + ':' + (e.lineno || ''));
  });
  addEventListener('unhandledrejection', function(e){
    var why = e.reason;
    report(why && why.message ? why.message : String(why),
           why && why.stack ? why.stack : '');
  });

  init();
})();
