(function () {
  var root = document.getElementById('chat-root');
  var ME = parseInt(root.dataset.me, 10);
  var peers = [], groups = [], mode = 'people', filter = 'all', query = '';
  var active = null;                       // {kind:'user'|'group', id, obj}
  var ws = null, retry = 0, typingTimer = null, lastTypingSent = 0, lastDay = '', bubbles = {};
  var GROUPS = root.dataset.apiGroups;
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function fmtTime(iso) { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
  function fmtDay(iso) {
    var d = new Date(iso), t = new Date();
    if (d.toDateString() === t.toDateString()) return 'Today';
    var y = new Date(); y.setDate(t.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'Yesterday';
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function peerById(id) { for (var i = 0; i < peers.length; i++) if (peers[i].id === id) return peers[i]; return null; }
  function groupById(id) { for (var i = 0; i < groups.length; i++) if (groups[i].id === id) return groups[i]; return null; }
  function api(url, opts) {
    opts = opts || {}; opts.credentials = 'same-origin';
    opts.headers = Object.assign({ 'X-CSRFToken': root.dataset.csrf }, opts.headers || {});
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (j) {
        if (r.status === 401) { location.href = '/login/'; throw new Error('auth'); }
        if (r.status === 403 && /password/i.test(j.error || '')) { location.href = '/password/'; throw new Error('pw'); }
        return { ok: r.ok, data: j };
      });
    });
  }
  function postJSON(url, obj) {
    return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj || {}) });
  }

  // ================= lists =================
  function loadPeers() {
    return api(root.dataset.apiEmp + '?q=' + encodeURIComponent(mode === 'people' ? query : '')).then(function (res) {
      peers = res.data.employees || []; renderList();
    });
  }
  function loadGroups() {
    return api(GROUPS).then(function (res) {
      groups = res.data.groups || [];
      if (active && active.kind === 'group' && !groupById(active.id)) showEmpty();   // I was removed / group deleted
      renderList();
    });
  }
  function renderList() {
    var list = $('c-list'); list.textContent = '';
    if (mode === 'people') renderPeers(list); else renderGroups(list);
  }
  function renderPeers(list) {
    $('c-count').textContent = String(peers.length);
    var shown = peers.filter(function (p) {
      return filter === 'all' || (filter === 'online' && p.online) || (filter === 'unread' && p.unread > 0);
    });
    if (!shown.length) { list.appendChild(el('p', 'muted pad', query ? 'No one matches that name.' : 'Nobody here yet.')); return; }
    shown.forEach(function (p) {
      var row = el('div', 'c-item' + (active && active.kind === 'user' && active.id === p.id ? ' on' : ''));
      var av = el('span', 'avatar', p.initials);
      var info = el('div', 'info'); info.appendChild(el('strong', '', p.name));
      var sub = p.last_message || [p.job_title, p.department].filter(Boolean).join(' \u00b7 ') || 'Employee';
      info.appendChild(el('small', '', sub));
      row.appendChild(av); row.appendChild(info);
      if (p.unread) row.appendChild(el('span', 'badge', String(p.unread)));
      row.appendChild(el('i', 'sdot' + (p.online ? ' on' : ''))); row.title = p.online ? 'Online' : 'Offline';
      row.addEventListener('click', function () { openPeer(p.id); });
      list.appendChild(row);
    });
  }
  function renderGroups(list) {
    var q = query.toLowerCase();
    var shown = groups.filter(function (g) { return !q || g.name.toLowerCase().indexOf(q) !== -1; });
    $('c-count').textContent = String(groups.length);
    if (!shown.length) { list.appendChild(el('p', 'muted pad', q ? 'No group matches that name.' : 'No groups yet. Click "+ New group".')); return; }
    shown.forEach(function (g) {
      var row = el('div', 'c-item' + (active && active.kind === 'group' && active.id === g.id ? ' on' : ''));
      var av = el('span', 'avatar gav', g.initials);
      var info = el('div', 'info'); info.appendChild(el('strong', '', g.name));
      info.appendChild(el('small', '', g.last_message || (g.members + ' members')));
      row.appendChild(av); row.appendChild(info);
      if (g.unread) row.appendChild(el('span', 'badge', String(g.unread)));
      row.addEventListener('click', function () { openGroup(g.id); });
      list.appendChild(row);
    });
  }
  function setMode(m) {
    mode = m; query = ''; $('c-q').value = ''; $('c-clear').hidden = true;
    document.querySelectorAll('.mode').forEach(function (b) { b.classList.toggle('on', b.dataset.m === m); });
    $('c-tabs').hidden = m !== 'people'; $('g-newbox').hidden = m !== 'groups';
    $('c-title-text').textContent = m === 'people' ? 'CONTACTS' : 'GROUPS';
    $('c-q').placeholder = m === 'people' ? 'Find employee\u2026' : 'Find group\u2026';
    return m === 'people' ? loadPeers() : loadGroups();
  }

  // ================= thread =================
  function showEmpty() { active = null; $('thread').hidden = true; $('empty').hidden = false; }
  function header(initials, name, sub, online, isGroup) {
    var av = $('t-av'); av.textContent = initials; av.className = 'avatar' + (isGroup ? ' gav' : '');
    if (!isGroup) av.appendChild(el('i', 'dot' + (online ? ' on' : '')));
    $('t-name').textContent = name; $('t-sub').textContent = sub; $('t-members').hidden = !isGroup;
  }
  function peerSub(p) { return (p.online ? 'Active now' : 'Offline') + (p.job_title ? ' - ' + p.job_title : ''); }
  function scrollDown() { var b = $('t-msgs'); b.scrollTop = b.scrollHeight; }
  function resetMessages(list) {
    $('t-msgs').textContent = ''; lastDay = ''; bubbles = {};
    list.forEach(addBubble); scrollDown();
  }
  function setGone(b) {
    var t = b.div.querySelector('.txt'); t.textContent = 'This message was deleted'; t.className = 'txt gone';
    var d = b.div.querySelector('.del'); if (d) d.remove();
    var k = b.div.querySelector('.tick'); if (k) k.remove();
    b.m.deleted = true;
  }
  function addBubble(m) {
    var box = $('t-msgs'), day = fmtDay(m.created_at), mine = m.sender === ME;
    if (day !== lastDay) { box.appendChild(el('div', 'day', day)); lastDay = day; }
    var b = el('div', 'bub' + (mine ? ' me' : ''));
    if (m.group && !mine) b.appendChild(el('div', 'who', m.sender_name));
    var txt = el('div', 'txt'); b.appendChild(txt);
    var meta = el('div', 'meta'); meta.appendChild(el('span', 'time', fmtTime(m.created_at)));
    if (mine && !m.group) {
      var tick = el('span', 'tick' + (m.read ? ' seen' : ''), m.read ? '\u2713\u2713' : '\u2713');
      tick.title = m.read ? 'Seen' : 'Sent'; meta.appendChild(tick);
    }
    if (mine && !m.deleted) {
      var del = el('button', 'del', 'Delete'); del.type = 'button';
      del.addEventListener('click', function () {
        if (confirm('Delete this message for everyone?')) wsSend({ type: 'delete', id: m.id });
      });
      meta.appendChild(del);
    }
    b.appendChild(meta); box.appendChild(b);
    var rec = { div: b, m: m }; bubbles[m.id] = rec;
    if (m.deleted) setGone(rec); else txt.appendChild(document.createTextNode(m.body));  // text node: never parsed as HTML
  }
  function openPeer(id) {
    if (mode !== 'people') { setMode('people'); }
    return api(root.dataset.apiMsg.replace('/0/', '/' + id + '/')).then(function (res) {
      if (!res.ok) return;
      var p = peerById(id) || res.data.peer;
      if (!peerById(id)) peers.unshift(p);
      p.unread = 0; active = { kind: 'user', id: id, obj: p };
      $('empty').hidden = true; $('thread').hidden = false;
      header(p.initials, p.name, peerSub(p), p.online, false);
      resetMessages(res.data.messages);
      $('t-typing').hidden = true; $('t-input').focus(); renderList();
    });
  }
  function openGroup(id) {
    if (mode !== 'groups') { setMode('groups'); }
    return api(GROUPS + id + '/').then(function (res) {
      if (!res.ok) return;
      var g = res.data.group; active = { kind: 'group', id: id, obj: g };
      var li = groupById(id); if (li) { li.unread = 0; li.members = g.members.length; }
      $('empty').hidden = true; $('thread').hidden = false;
      header(g.initials, g.name, g.members.length + ' members', false, true);
      resetMessages(res.data.messages);
      $('t-typing').hidden = true; $('t-input').focus(); renderList();
    });
  }
  function refreshGroupHeader() {
    if (!active || active.kind !== 'group') return;
    api(GROUPS + active.id + '/').then(function (res) {
      if (!res.ok || !active || active.kind !== 'group') return;
      active.obj = res.data.group; $('t-sub').textContent = active.obj.members.length + ' members';
    });
  }

  // ================= websocket =================
  function wsSend(obj) {
    if (!ws || ws.readyState !== 1) { $('conn').hidden = false; return false; }
    ws.send(JSON.stringify(obj)); return true;
  }
  function connect() {
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + location.host + '/ws/chat/');
    ws.onopen = function () { retry = 0; $('conn').hidden = true; if (window.setNet) window.setNet(true); };
    ws.onclose = function () { $('conn').hidden = false; if (window.setNet) window.setNet(false); setTimeout(connect, Math.min(10000, 1000 * Math.pow(2, retry++))); };
    ws.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === 'message') (d.group ? onGroupMessage : onMessage)(d);
      else if (d.type === 'presence') onPresence(d);
      else if (d.type === 'typing') onTyping(d);
      else if (d.type === 'seen') onSeen(d);
      else if (d.type === 'deleted') onDeleted(d);
      else if (d.type === 'groups_changed') { loadGroups().then(refreshGroupHeader); }
      else if (d.type === 'error') alert(d.error);
    };
  }
  function onMessage(m) {
    var peerId = m.sender === ME ? m.recipient : m.sender, p = peerById(peerId);
    if (!p) { loadPeers(); return; }
    p.last_message = (m.sender === ME ? 'You: ' : '') + m.body.slice(0, 60); p.last_at = m.created_at;
    peers.splice(peers.indexOf(p), 1); peers.unshift(p);
    if (active && active.kind === 'user' && active.id === peerId) {
      addBubble(m); scrollDown();
      if (m.sender !== ME) wsSend({ type: 'read', peer: peerId });
      $('t-typing').hidden = true;
    } else if (m.sender !== ME) { p.unread = (p.unread || 0) + 1; }
    if (mode === 'people') renderList();
  }
  function onGroupMessage(m) {
    var g = groupById(m.group);
    if (!g) { loadGroups(); return; }
    var who = m.sender === ME ? 'You' : (m.sender_name || '').split(' ')[0];
    g.last_message = who + ': ' + m.body.slice(0, 50); g.last_at = m.created_at;
    groups.splice(groups.indexOf(g), 1); groups.unshift(g);
    if (active && active.kind === 'group' && active.id === m.group) {
      addBubble(m); scrollDown();
      if (m.sender !== ME) wsSend({ type: 'read_group', group: m.group });
    } else if (m.sender !== ME) { g.unread = (g.unread || 0) + 1; }
    if (mode === 'groups') renderList();
  }
  function onPresence(d) {
    var p = peerById(d.user_id); if (!p) return;
    p.online = d.online; if (mode === 'people') renderList();
    if (active && active.kind === 'user' && active.id === p.id) header(p.initials, p.name, peerSub(p), p.online, false);
  }
  function onTyping(d) {
    if (!active || active.kind !== 'user' || active.id !== d.from) return;
    $('t-typing').hidden = false; clearTimeout(typingTimer);
    typingTimer = setTimeout(function () { $('t-typing').hidden = true; }, 3000);
  }
  function onSeen(d) {   // the other person opened our conversation: ticks turn blue
    if (!active || active.kind !== 'user' || active.id !== d.by) return;
    Object.keys(bubbles).forEach(function (id) {
      var b = bubbles[id];
      if (b.m.sender === ME && !b.m.read) {
        b.m.read = true; var k = b.div.querySelector('.tick');
        if (k) { k.textContent = '\u2713\u2713'; k.className = 'tick seen'; k.title = 'Seen'; }
      }
    });
  }
  function onDeleted(d) {
    var b = bubbles[d.id]; if (b) setGone(b);
    loadPeers(); loadGroups();            // refresh the "last message" previews
  }
  setInterval(function () { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);

  function send() {
    var box = $('t-input'), body = box.value.trim();
    if (!body || !active) return;
    var ok = active.kind === 'user'
      ? wsSend({ type: 'message', to: active.id, body: body })
      : wsSend({ type: 'group_message', group: active.id, body: body });
    if (ok) { box.value = ''; box.style.height = 'auto'; }
  }
  $('t-send').addEventListener('click', send);
  $('t-input').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('t-input').addEventListener('input', function () {
    this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 140) + 'px';
    var now = Date.now();
    if (active && active.kind === 'user' && now - lastTypingSent > 2000) { lastTypingSent = now; wsSend({ type: 'typing', to: active.id }); }
  });

  // ================= search, tabs, mode =================
  var st = null;
  $('c-clear').addEventListener('click', function () { var q = $('c-q'); q.value = ''; q.dispatchEvent(new Event('input')); q.focus(); });
  $('c-q').addEventListener('input', function () {
    var v = this.value; $('c-clear').hidden = !v; clearTimeout(st);
    st = setTimeout(function () { query = v.trim(); if (mode === 'people') loadPeers(); else renderList(); }, 250);
  });
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('on'); });
      t.classList.add('on'); filter = t.dataset.f; renderList();
    });
  });
  document.querySelectorAll('.mode').forEach(function (b) { b.addEventListener('click', function () { setMode(b.dataset.m); }); });

  // ================= modals =================
  function openModal(build) { var body = $('modal-body'); body.textContent = ''; build(body); $('modal').hidden = false; }
  function closeModal() { $('modal').hidden = true; }
  $('modal').addEventListener('click', function (e) { if (e.target === this) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });
  function checkRows(container, people, exclude) {
    var rows = [];
    people.forEach(function (p) {
      if (exclude && exclude.indexOf(p.id) !== -1) return;
      var lab = el('label', 'chk'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = p.id;
      lab.appendChild(cb);
      var s = el('span'); s.appendChild(el('b', '', p.name)); s.appendChild(el('small', '', p.job_title || p.department || ''));
      lab.appendChild(s); container.appendChild(lab); rows.push({ lab: lab, cb: cb, name: p.name.toLowerCase() });
    });
    return rows;
  }
  function filterRows(rows, q) { q = q.toLowerCase(); rows.forEach(function (r) { r.lab.hidden = q && r.name.indexOf(q) === -1; }); }
  function checked(rows) { return rows.filter(function (r) { return r.cb.checked; }).map(function (r) { return parseInt(r.cb.value, 10); }); }

  function newGroupModal() {
    api(root.dataset.apiEmp).then(function (res) {
      var all = res.data.employees || [];
      openModal(function (b) {
        b.appendChild(el('h3', '', 'Create group'));
        var name = document.createElement('input'); name.placeholder = 'Group name'; name.maxLength = 80; b.appendChild(name);
        var find = document.createElement('input'); find.placeholder = 'Search people'; b.appendChild(find);
        var box = el('div', 'chk-list'); b.appendChild(box);
        var rows = checkRows(box, all);
        find.addEventListener('input', function () { filterRows(rows, find.value); });
        var err = el('div', 'field-err'); b.appendChild(err);
        var act = el('div', 'm-actions'), cancel = el('button', 'btn ghost', 'Cancel'), ok = el('button', 'btn primary', 'Create group');
        cancel.type = ok.type = 'button'; act.appendChild(cancel); act.appendChild(ok); b.appendChild(act);
        cancel.addEventListener('click', closeModal);
        ok.addEventListener('click', function () {
          postJSON(GROUPS, { name: name.value, member_ids: checked(rows) }).then(function (r) {
            if (!r.ok) { err.textContent = r.data.error || 'Could not create the group.'; return; }
            closeModal(); loadGroups().then(function () { openGroup(r.data.id); });
          });
        });
        name.focus();
      });
    });
  }
  function membersModal() {
    if (!active || active.kind !== 'group') return;
    var g = active.obj, gid = active.id;
    api(root.dataset.apiEmp).then(function (res) {
      var all = res.data.employees || [], memberIds = g.members.map(function (m) { return m.id; });
      openModal(function (b) {
        b.appendChild(el('h3', '', g.name));
        b.appendChild(el('p', 'muted', g.members.length + ' members'));
        var list = el('div', 'chk-list');
        g.members.forEach(function (m) {
          var row = el('div', 'mrow'); row.appendChild(el('span', 'avatar sm', m.initials));
          var info = el('span', 'minfo'); info.appendChild(el('b', '', m.name + (m.id === ME ? ' (you)' : '')));
          info.appendChild(el('small', '', m.id === g.owner_id ? 'Group owner' : (m.job_title || 'Member'))); row.appendChild(info);
          if (g.is_owner && m.id !== ME) {
            var rm = el('button', 'btn ghost sm', 'Remove'); rm.type = 'button';
            rm.addEventListener('click', function () {
              if (!confirm('Remove ' + m.name + ' from this group?')) return;
              postJSON(GROUPS + gid + '/members/' + m.id + '/remove/').then(function () { closeModal(); openGroup(gid); });
            });
            row.appendChild(rm);
          }
          list.appendChild(row);
        });
        b.appendChild(list);
        var addRows = [];
        if (g.is_owner) {
          b.appendChild(el('h4', '', 'Add people'));
          var box = el('div', 'chk-list'); b.appendChild(box);
          addRows = checkRows(box, all, memberIds);
          if (!addRows.length) box.appendChild(el('p', 'muted', 'Everyone is already in this group.'));
        }
        var act = el('div', 'm-actions');
        var leave = el('button', 'btn danger', 'Leave group'), close = el('button', 'btn ghost', 'Close');
        leave.type = close.type = 'button'; act.appendChild(leave);
        if (g.is_owner && addRows.length) {
          var add = el('button', 'btn primary', 'Add selected'); add.type = 'button'; act.appendChild(add);
          add.addEventListener('click', function () {
            var ids = checked(addRows); if (!ids.length) return;
            postJSON(GROUPS + gid + '/members/', { user_ids: ids }).then(function () { closeModal(); openGroup(gid); });
          });
        }
        act.appendChild(close); b.appendChild(act);
        close.addEventListener('click', closeModal);
        leave.addEventListener('click', function () {
          if (!confirm('Leave "' + g.name + '"?')) return;
          postJSON(GROUPS + gid + '/members/' + ME + '/remove/').then(function () { closeModal(); showEmpty(); loadGroups(); });
        });
      });
    });
  }
  $('g-new').addEventListener('click', newGroupModal);
  $('t-members').addEventListener('click', membersModal);

  // ================= start =================
  var w = parseInt(root.dataset.with, 10), gq = parseInt(root.dataset.group, 10);
  Promise.all([loadPeers(), loadGroups()]).then(function () {
    if (gq) openGroup(gq); else if (w) openPeer(w);
  });
  connect();
})();
