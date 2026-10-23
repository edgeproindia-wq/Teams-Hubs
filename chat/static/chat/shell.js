/* Runs on every page after login: ONE shared live connection, connection status,
   pop-up alerts for new messages, browser notifications, sound and tab-title counter. */
(function () {
  var body = document.body, ME = parseInt(body.dataset.me, 10) || 0;
  var dot = document.getElementById('net-dot'), txt = document.getElementById('net-text');
  var fdot = document.getElementById('foot-dot'), ftxt = document.getElementById('foot-text');
  var baseTitle = document.title, hiddenCount = 0, bus = {}, ws = null, retry = 0;

  function set(ok, label) {
    [dot, fdot].forEach(function (d) { if (d) d.className = 'dot' + (ok ? ' on' : ''); });
    if (txt) txt.textContent = label || (ok ? 'Connected' : 'Offline');
    if (ftxt) ftxt.textContent = ok ? 'Connected to company server' : 'Not connected - check your network';
  }
  window.addEventListener('online', function () { set(true); });
  window.addEventListener('offline', function () { set(false); });
  set(navigator.onLine !== false);

  // ---------- tiny event bus (chat.js listens here) ----------
  function emit(type, data) { (bus[type] || []).forEach(function (fn) { try { fn(data); } catch (e) { console.error(e); } }); }
  window.EdgeWS = {
    on: function (type, fn) { (bus[type] = bus[type] || []).push(fn); },
    send: function (obj) { if (!ws || ws.readyState !== 1) return false; ws.send(JSON.stringify(obj)); return true; },
    ready: function () { return !!ws && ws.readyState === 1; }
  };

  // ---------- pop-up toast, sound, browser notification ----------
  var toasts = document.getElementById('toasts');
  function beep() {
    try {
      var C = window.AudioContext || window.webkitAudioContext; if (!C) return;
      var a = beep.ctx || (beep.ctx = new C()); if (a.state === 'suspended') a.resume();
      var o = a.createOscillator(), g = a.createGain(), t = a.currentTime;
      o.type = 'sine'; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.15, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
      o.connect(g); g.connect(a.destination); o.start(t); o.stop(t + 0.4);
    } catch (e) { /* sound is optional */ }
  }
  function openConversation(m) {
    var kind = m.group ? 'group' : 'user', id = m.group ? m.group : m.sender;
    if (window.EdgeOpen) window.EdgeOpen(kind, id);
    else location.href = '/chat/?' + (m.group ? 'group=' : 'with=') + id;
  }
  function showToast(m, title, preview) {
    if (!toasts) return;
    while (toasts.children.length >= 4) toasts.removeChild(toasts.firstChild);
    var t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'alert');
    var h = document.createElement('b'); h.textContent = title;
    var p = document.createElement('span'); p.textContent = preview;
    var x = document.createElement('button'); x.type = 'button'; x.className = 'tx'; x.textContent = '\u00d7'; x.title = 'Dismiss';
    t.appendChild(h); t.appendChild(p); t.appendChild(x); toasts.appendChild(t);
    function close() { if (t.parentNode) t.parentNode.removeChild(t); }
    x.addEventListener('click', function (e) { e.stopPropagation(); close(); });
    t.addEventListener('click', function () { close(); openConversation(m); });
    setTimeout(close, 7000);
  }
  function browserNotify(m, title, preview) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      var n = new Notification(title, { body: preview, tag: m.group ? 'g' + m.group : 'u' + m.sender });
      n.onclick = function () { window.focus(); openConversation(m); n.close(); };
    } catch (e) { /* some browsers only allow notifications from a service worker */ }
  }
  function onIncoming(m) {
    if (m.sender === ME || m.deleted) return;
    var a = window.EdgeActive, same = a && (m.group ? (a.kind === 'group' && a.id === m.group)
                                                    : (a.kind === 'user' && a.id === m.sender));
    var title = m.group ? (m.group_name + ' \u00b7 ' + m.sender_name) : m.sender_name;
    var preview = (m.body || '').slice(0, 90);
    if (!m.group) { var s = document.getElementById('stat-unread'); if (s) s.textContent = String((parseInt(s.textContent, 10) || 0) + 1); }
    if (document.hidden) {                       // tab in background: desktop notification + title counter
      hiddenCount++; document.title = '(' + hiddenCount + ') ' + baseTitle;
      browserNotify(m, title, preview); beep();
    } else if (!same) {                          // looking at another page/conversation: in-page pop-up
      showToast(m, title, preview); beep();
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { hiddenCount = 0; document.title = baseTitle; }
    emit('visible', null);
  });

  // ---------- "Enable alerts" button (browsers need a click to allow notifications) ----------
  var nb = document.getElementById('notif-btn');
  if (nb && 'Notification' in window && Notification.permission === 'default') {
    nb.hidden = false;
    nb.addEventListener('click', function () {
      Notification.requestPermission().then(function () { nb.hidden = true; });
    });
  }

  // ---------- the one shared live connection ----------
  function connect() {
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + location.host + '/ws/chat/');
    ws.onopen = function () { retry = 0; set(true, 'Connected'); emit('open', null); };
    ws.onclose = function () {
      set(false, 'Reconnecting...'); emit('close', null);
      setTimeout(connect, Math.min(10000, 1000 * Math.pow(2, retry++)));
    };
    ws.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === 'message') onIncoming(d);
      emit(d.type, d);
    };
  }
  if (body.dataset.ws === '1') {
    connect();
    setInterval(function () { window.EdgeWS.send({ type: 'ping' }); }, 25000);
  }
})();
