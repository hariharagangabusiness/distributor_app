// List / Grid view switch for record-list tables.
//
// Opt in per table with data-view-toggle="<key>" (any short, stable string —
// the module name is a natural choice, e.g. data-view-toggle="Customer").
// The table must already carry the table-cards-mobile class and per-cell
// data-label attributes (see the "Mobile card-view tables" block in
// style.css) — this reuses that exact label/value markup for the grid
// view's cards instead of requiring page-specific layout.
//
// Adds a small List/Grid switch above the table and remembers the choice
// per browser (localStorage), scoped to that table's key, so it's restored
// on the next visit — the same pattern this app already uses for the
// dark-mode toggle and the collapsed sidebar sections.
(function () {
  var STORAGE_PREFIX = 'listViewMode:';

  function applyView(table, toggle, view) {
    table.classList.toggle('view-grid', view === 'grid');
    var buttons = toggle.querySelectorAll('button[data-view]');
    for (var i = 0; i < buttons.length; i++) {
      var active = buttons[i].getAttribute('data-view') === view;
      buttons[i].classList.toggle('active', active);
      buttons[i].setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }

  function setUp(table) {
    var key = STORAGE_PREFIX + table.getAttribute('data-view-toggle');
    var wrapper = table.closest('.table-responsive') || table;

    var toggle = document.createElement('div');
    toggle.className = 'list-view-toggle';
    toggle.setAttribute('role', 'group');
    toggle.setAttribute('aria-label', 'Switch between list and grid view');
    toggle.innerHTML =
      '<button type="button" data-view="list" title="List view" aria-label="List view"><i class="bi bi-list-ul"></i></button>' +
      '<button type="button" data-view="grid" title="Grid view" aria-label="Grid view"><i class="bi bi-grid-3x3-gap-fill"></i></button>';

    var bar = document.createElement('div');
    bar.className = 'list-view-toggle-bar';
    bar.appendChild(toggle);
    wrapper.parentNode.insertBefore(bar, wrapper);

    var saved = 'list';
    try { saved = localStorage.getItem(key) || 'list'; } catch (e) {}
    applyView(table, toggle, saved);

    toggle.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-view]') : null;
      if (!btn) return;
      var view = btn.getAttribute('data-view');
      applyView(table, toggle, view);
      try { localStorage.setItem(key, view); } catch (err) {}
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var tables = document.querySelectorAll('table.table-cards-mobile[data-view-toggle]');
    tables.forEach(setUp);
  });
})();
