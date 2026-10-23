(function () {
  var dot = document.getElementById('net-dot'), txt = document.getElementById('net-text');
  var fdot = document.getElementById('foot-dot'), ftxt = document.getElementById('foot-text');
  function set(ok, label) {
    [dot, fdot].forEach(function (d) { if (d) d.className = 'dot' + (ok ? ' on' : ''); });
    if (txt) txt.textContent = label || (ok ? 'Connected' : 'Offline');
    if (ftxt) ftxt.textContent = ok ? 'Connected to company server' : 'Not connected - check your network';
  }
  // The chat page calls this with the real WebSocket state.
  window.setNet = function (ok) { set(ok, ok ? 'Connected' : 'Reconnecting...'); };
  window.addEventListener('online', function () { set(true); });
  window.addEventListener('offline', function () { set(false); });
  set(navigator.onLine !== false);
})();
