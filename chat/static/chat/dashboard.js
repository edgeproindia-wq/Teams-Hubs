(function () {
  var box = document.getElementById('online-list');
  if (!box) return;
  function esc(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
  function refresh() {
    fetch(box.dataset.endpoint, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      var list = d.employees || [];
      var counter = document.getElementById('stat-online');
      if (counter) counter.textContent = list.length + 1;
      if (!list.length) { box.innerHTML = '<p class="muted pad">No one else is online right now.</p>'; return; }
      box.innerHTML = list.slice(0, 12).map(function (u) {
        return '<a class="person" href="/chat/?with=' + u.id + '"><span class="avatar">' + esc(u.initials) +
          '<i class="dot on"></i></span><div><strong>' + esc(u.name) + '</strong><small>' + esc(u.job_title || 'Employee') + '</small></div></a>';
      }).join('');
    }).catch(function () {});
  }
  setInterval(refresh, 20000);
})();
