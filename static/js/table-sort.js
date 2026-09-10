// Adds click-to-sort to every data table in the app (alphabetical, numeric,
// or oldest/newest date) without needing any changes on a page-by-page
// basis. Any <table> that has a <thead> gets this automatically; opt a
// whole table out with class="no-sort-table" on the <table>, or opt out a
// single column with class="no-sort" on its <th> (already used for
// actions-only columns like View/Edit/Delete buttons).
//
// Click a header once to sort ascending, click again to reverse. A small
// caret shows the active sort column and direction. Values are read from
// each cell's text, with best-effort type detection:
//   - a <th data-sort-type="date"> or "number" forces that parsing mode
//   - otherwise: numbers/currency (₹, commas, %) sort numerically, a
//     DD-MM-YYYY (optionally with HH:MM) string sorts as a date, anything
//     else sorts as plain text (case-insensitive)
// A blank/"-" cell always sorts to the bottom, in either direction, so an
// oldest-date or highest-amount sort isn't thrown off by rows with no value.
(function () {
  function parseCell(text, forcedType) {
    var raw = (text || '').trim();
    if (!raw || raw === '-') return { empty: true };

    if (forcedType === 'date' || (!forcedType && /^\d{2}-\d{2}-\d{4}(\s+\d{2}:\d{2})?$/.test(raw))) {
      var m = raw.match(/^(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{2}):(\d{2}))?/);
      if (m) {
        var t = new Date(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0)).getTime();
        return { empty: false, value: t };
      }
      return { empty: true };
    }

    if (forcedType === 'number' || (!forcedType && /^[₹$\s]*-?[\d,]+(\.\d+)?%?$/.test(raw))) {
      var cleaned = raw.replace(/[₹$,%\s]/g, '');
      var n = parseFloat(cleaned);
      if (!isNaN(n)) return { empty: false, value: n };
    }

    return { empty: false, value: raw.toLowerCase() };
  }

  function compareRows(aText, bText, forcedType, dir) {
    var a = parseCell(aText, forcedType), b = parseCell(bText, forcedType);
    if (a.empty && b.empty) return 0;
    if (a.empty) return 1;   // blanks always sink to the bottom
    if (b.empty) return -1;
    if (a.value < b.value) return -1 * dir;
    if (a.value > b.value) return 1 * dir;
    return 0;
  }

  function wireTable(table) {
    var thead = table.querySelector('thead');
    var tbody = table.querySelector('tbody');
    if (!thead || !tbody) return;
    var headerRow = thead.rows[thead.rows.length - 1];
    if (!headerRow) return;

    Array.prototype.forEach.call(headerRow.cells, function (th, colIndex) {
      if (th.classList.contains('no-sort') || !th.textContent.trim()) return;
      th.style.cursor = 'pointer';
      th.title = 'Click to sort';
      var caret = document.createElement('span');
      caret.className = 'sort-caret text-muted small ms-1';
      th.appendChild(caret);

      th.addEventListener('click', function () {
        var dir = th.getAttribute('data-sort-dir') === 'asc' ? -1 : 1;
        Array.prototype.forEach.call(headerRow.cells, function (other) {
          other.removeAttribute('data-sort-dir');
          var c = other.querySelector('.sort-caret');
          if (c) c.textContent = '';
        });
        th.setAttribute('data-sort-dir', dir === 1 ? 'asc' : 'desc');
        caret.textContent = dir === 1 ? '▲' : '▼';

        var forcedType = th.getAttribute('data-sort-type');
        var rows = Array.prototype.slice.call(tbody.rows).filter(function (r) {
          return r.cells.length > 1 || (r.cells[0] && !r.cells[0].hasAttribute('colspan'));
        });
        rows.sort(function (r1, r2) {
          var c1 = r1.cells[colIndex], c2 = r2.cells[colIndex];
          if (!c1 || !c2) return 0;
          return compareRows(c1.textContent, c2.textContent, forcedType, dir);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table').forEach(function (table) {
      if (table.classList.contains('no-sort-table')) return;
      // Data-entry tables (line items on New Sale, Customize Columns' own
      // checkboxes/order numbers, etc.) hold live <input>/<select> state that
      // a text-based sort can't see and reordering rows on click would only
      // confuse someone trying to fill in a form - skip those automatically.
      var body = table.querySelector('tbody');
      if (body && body.querySelector('input, select, textarea')) return;
      wireTable(table);
    });
  });
})();
