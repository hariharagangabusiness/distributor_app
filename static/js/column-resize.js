// Lets a user drag a table column's right edge to resize it, on every data
// table in the app, with no per-page work needed (same "just works
// everywhere" approach as table-sort.js). Widths are remembered per page +
// table in this browser (localStorage), keyed by column header text so a
// saved width still lines up correctly even after Customize Columns
// reorders things.
//
// Opt a whole table out with class="no-resize-table" on the <table>.
// Actions-only columns (View/Edit/Delete, no header text, or class
// "no-sort" which already marks a non-data column) don't get a handle.
(function () {
  var STORAGE_PREFIX = 'colwidths:';

  function storageKey(table) {
    var id = table.id || Array.prototype.indexOf.call(
      document.querySelectorAll('table'), table);
    return STORAGE_PREFIX + window.location.pathname + ':' + id;
  }

  function loadWidths(table) {
    try {
      var raw = window.localStorage.getItem(storageKey(table));
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveWidths(table, widths) {
    try {
      window.localStorage.setItem(storageKey(table), JSON.stringify(widths));
    } catch (e) {
      // Private browsing / storage disabled / quota full - resizing still
      // works for the rest of this page view, it just won't be remembered.
    }
  }

  function headerKey(th) {
    var clone = th.cloneNode(true);
    var handle = clone.querySelector('.col-resize-handle');
    if (handle) handle.remove();
    var caret = clone.querySelector('.sort-caret');
    if (caret) caret.remove();
    return clone.textContent.trim() || ('col' + Array.prototype.indexOf.call(th.parentNode.children, th));
  }

  function applyWidth(table, th, px) {
    th.style.width = px + 'px';
    table.classList.add('col-resize-active');
  }

  function wireTable(table) {
    var thead = table.querySelector('thead');
    if (!thead) return;
    var headerRow = thead.rows[thead.rows.length - 1];
    if (!headerRow) return;

    var widths = loadWidths(table);
    var hasSaved = false;

    Array.prototype.forEach.call(headerRow.cells, function (th) {
      if (!th.textContent.trim()) return; // an actions-only column, nothing to grab onto
      var key = headerKey(th);
      if (widths[key]) {
        applyWidth(table, th, widths[key]);
        hasSaved = true;
      }

      var handle = document.createElement('span');
      handle.className = 'col-resize-handle';
      th.appendChild(handle);

      var startX, startWidth;
      function onMove(e) {
        var clientX = e.touches ? e.touches[0].clientX : e.clientX;
        var newWidth = Math.max(40, startWidth + (clientX - startX));
        applyWidth(table, th, newWidth);
      }
      function onUp() {
        handle.classList.remove('resizing');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onUp);
        var widths2 = loadWidths(table);
        widths2[key] = th.offsetWidth;
        saveWidths(table, widths2);
      }
      function onDown(e) {
        e.preventDefault();
        e.stopPropagation(); // don't also trigger the click-to-sort handler on this <th>
        startX = e.touches ? e.touches[0].clientX : e.clientX;
        startWidth = th.offsetWidth;
        handle.classList.add('resizing');
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onMove);
        document.addEventListener('touchend', onUp);
      }
      handle.addEventListener('mousedown', onDown);
      handle.addEventListener('touchstart', onDown);
      handle.addEventListener('click', function (e) { e.stopPropagation(); });
    });

    if (hasSaved) table.classList.add('col-resize-active');
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table').forEach(function (table) {
      if (table.classList.contains('no-resize-table')) return;
      var thead = table.querySelector('thead');
      if (!thead) return;
      wireTable(table);
    });
  });
})();
