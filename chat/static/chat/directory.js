(function () {
  var grid = document.getElementById('dir-grid');
  var q = document.getElementById('dir-q');
  var filter = 'all', timer = null;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function render(list) {
    grid.textContent = '';
    if (!list.length) { grid.appendChild(el('p', 'muted', 'No employees found.')); return; }
    list.forEach(function (u) {
      var card = el('div', 'dcard'), top = el('div', 'top');
      var av = el('span', 'avatar', u.initials); av.appendChild(el('i', 'dot' + (u.online ? ' on' : '')));
      var info = el('div'); info.appendChild(el('strong', '', u.name));
      info.appendChild(el('small', '', (u.job_title || 'Employee') + (u.department ? ' / ' + u.department : '')));
      info.appendChild(el('small', '', u.online ? 'Active now' : 'Offline'));
      top.appendChild(av); top.appendChild(info); card.appendChild(top);
      var a = el('a', 'btn primary sm', 'Message'); a.href = grid.dataset.chat + '?with=' + u.id; card.appendChild(a);
      grid.appendChild(card);
    });
  }
  function load() {
    var url = grid.dataset.endpoint + '?q=' + encodeURIComponent(q.value) + (filter === 'online' ? '&online=1' : '');
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      .then(function (d) { render(d.employees || []); }).catch(function () {});
  }
  q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(load, 250); });
  document.querySelectorAll('input[name=flt]').forEach(function (r) {
    r.addEventListener('change', function () { filter = r.value; load(); });
  });
  load(); setInterval(load, 20000);
})();
