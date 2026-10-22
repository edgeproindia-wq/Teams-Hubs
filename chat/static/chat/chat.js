(function () {
  var root = document.getElementById('chat-root');
  var ME = parseInt(root.dataset.me, 10);
  var peers = [], active = null, filter = 'all', query = '', ws = null, retry = 0, typingTimer = null, lastTypingSent = 0;
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
  function byId(id) { for (var i = 0; i < peers.length; i++) if (peers[i].id === id) return peers[i]; return null; }
  function api(url, opts) {
    opts = opts || {}; opts.credentials = 'same-origin';
    opts.headers = Object.assign({ 'X-CSRFToken': root.dataset.csrf }, opts.headers || {});
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { location.href = '/login/'; throw new Error('auth'); }
      return r.json().then(function (j) { return { ok: r.ok, data: j }; });
    });
  }

  // ---------- contacts ----------
  function loadPeers() {
    return api(root.dataset.apiEmp + '?q=' + encodeURIComponent(query)).then(function (res) {
      peers = res.data.employees || []; renderList();
    });
  }
  function renderList() {
    var list = $('c-list'); list.textContent = '';
    var shown = peers.filter(function (p) {
      return filter === 'all' || (filter === 'online' && p.online) || (filter === 'unread' && p.unread > 0);
    });
    if (!shown.length) { list.appendChild(el('p', 'muted pad', query ? 'No one matches that name.' : 'Nobody here yet.')); return; }
    shown.forEach(function (p) {
      var row = el('div', 'c-item' + (active && active.id === p.id ? ' on' : ''));
      var av = el('span', 'avatar', p.initials); av.appendChild(el('i', 'dot' + (p.online ? ' on' : '')));
      var info = el('div', 'info'); info.appendChild(el('strong', '', p.name));
      info.appendChild(el('small', '', p.last_message || p.job_title || 'Employee'));
      row.appendChild(av); row.appendChild(info);
      if (p.unread) row.appendChild(el('span', 'badge', String(p.unread)));
      row.addEventListener('click', function () { openPeer(p.id); });
      list.appendChild(row);
    });
  }

  // ---------- thread ----------
  function setHeader(p) {
    $('t-av').textContent = p.initials; $('t-av').appendChild(el('i', 'dot' + (p.online ? ' on' : '')));
    $('t-name').textContent = p.name;
    $('t-sub').textContent = (p.online ? 'Active now' : 'Offline') + (p.job_title ? ' - ' + p.job_title : '');
  }
  var lastDay = '';
  function addBubble(m) {
    var box = $('t-msgs'), day = fmtDay(m.created_at);
    if (day !== lastDay) { box.appendChild(el('div', 'day', day)); lastDay = day; }
    var b = el('div', 'bub' + (m.sender === ME ? ' me' : ''));
    b.appendChild(document.createTextNode(m.body));   // text node: message content is never parsed as HTML
    b.appendChild(el('small', '', fmtTime(m.created_at)));
    box.appendChild(b);
  }
  function scrollDown() { var b = $('t-msgs'); b.scrollTop = b.scrollHeight; }
  function openPeer(id) {
    return api(root.dataset.apiMsg.replace('/0/', '/' + id + '/')).then(function (res) {
      if (!res.ok) return;
      var p = byId(id) || res.data.peer;
      if (!byId(id)) peers.unshift(p);
      p.unread = 0; active = p;
      $('empty').hidden = true; $('thread').hidden = false; $('cp-out').hidden = true; $('cp-err').textContent = '';
      setHeader(p);
      $('t-msgs').textContent = ''; lastDay = '';
      res.data.messages.forEach(addBubble); scrollDown();
      $('t-typing').hidden = true; $('t-input').focus(); renderList();
    });
  }

  // ---------- websocket ----------
  function connect() {
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + location.host + '/ws/chat/');
    ws.onopen = function () { retry = 0; $('conn').hidden = true; };
    ws.onclose = function () { $('conn').hidden = false; setTimeout(connect, Math.min(10000, 1000 * Math.pow(2, retry++))); };
    ws.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === 'message') onMessage(d);
      else if (d.type === 'presence') onPresence(d);
      else if (d.type === 'typing') onTyping(d);
      else if (d.type === 'error') alert(d.error);
    };
  }
  function onMessage(m) {
    var peerId = m.sender === ME ? m.recipient : m.sender, p = byId(peerId);
    if (!p) { loadPeers(); return; }
    p.last_message = (m.sender === ME ? 'You: ' : '') + m.body.slice(0, 60); p.last_at = m.created_at;
    peers.splice(peers.indexOf(p), 1); peers.unshift(p);
    if (active && active.id === peerId) {
      addBubble(m); scrollDown();
      if (m.sender !== ME && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'read', peer: peerId }));
      $('t-typing').hidden = true;
    } else if (m.sender !== ME) { p.unread = (p.unread || 0) + 1; }
    renderList();
  }
  function onPresence(d) {
    var p = byId(d.user_id); if (!p) return;
    p.online = d.online; renderList();
    if (active && active.id === p.id) setHeader(p);
  }
  function onTyping(d) {
    if (!active || active.id !== d.from) return;
    $('t-typing').hidden = false; clearTimeout(typingTimer);
    typingTimer = setTimeout(function () { $('t-typing').hidden = true; }, 3000);
  }
  setInterval(function () { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);

  function send() {
    var box = $('t-input'), body = box.value.trim();
    if (!body || !active) return;
    if (!ws || ws.readyState !== 1) { $('conn').hidden = false; return; }
    ws.send(JSON.stringify({ type: 'message', to: active.id, body: body }));
    box.value = ''; box.style.height = 'auto';
  }
  $('t-send').addEventListener('click', send);
  $('t-input').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('t-input').addEventListener('input', function () {
    this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 140) + 'px';
    var now = Date.now();
    if (active && ws && ws.readyState === 1 && now - lastTypingSent > 2000) { lastTypingSent = now; ws.send(JSON.stringify({ type: 'typing', to: active.id })); }
  });

  // ---------- search & tabs ----------
  var st = null;
  $('c-q').addEventListener('input', function () { var v = this.value; clearTimeout(st); st = setTimeout(function () { query = v.trim(); loadPeers(); }, 250); });
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('on'); });
      t.classList.add('on'); filter = t.dataset.f; renderList();
    });
  });

  // ---------- AI copilot ----------
  $('ai-toggle').addEventListener('click', function () {
    if (window.innerWidth > 1100) root.classList.toggle('no-ai'); else $('copilot').classList.toggle('open');
  });
  document.querySelectorAll('.cp-actions button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!active) { $('cp-err').textContent = 'Open a conversation first.'; return; }
      $('cp-err').textContent = ''; var old = btn.textContent; btn.disabled = true; btn.textContent = 'Thinking...';
      api(root.dataset.apiAi, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: btn.dataset.a, text: $('t-input').value, tone: $('cp-tone').value, peer_id: active.id })
      }).then(function (res) {
        if (!res.ok) { $('cp-err').textContent = res.data.error || 'Something went wrong.'; $('cp-out').hidden = true; return; }
        $('cp-text').textContent = res.data.text; $('cp-out').hidden = false;
      }).catch(function () { $('cp-err').textContent = 'Could not reach the copilot.'; })
        .then(function () { btn.disabled = false; btn.textContent = old; });
    });
  });
  $('cp-use').addEventListener('click', function () {
    var t = $('t-input'); t.value = $('cp-text').textContent; t.dispatchEvent(new Event('input')); t.focus();
  });

  // ---------- start ----------
  loadPeers().then(function () {
    var w = parseInt(root.dataset.with, 10);
    if (w) openPeer(w);
  });
  connect();
})();
