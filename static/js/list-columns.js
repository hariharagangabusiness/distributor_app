// Applies saved column visibility/order to a list table.
// The calling page must set window.LIST_COLUMNS_TABLE_ID (the table's id)
// and window.LIST_COLUMNS_PREFS (an array of {key, visible, order}, already
// sorted by order) before this script runs. Table cells (both <th> in
// thead and <td> in tbody rows) must carry a matching data-col="<key>"
// attribute; any cell without data-col (e.g. an actions column) is left
// in place at the end of the row.
(function () {
  function applyListColumns(tableId, prefs) {
    var table = document.getElementById(tableId);
    if (!table || !prefs || !prefs.length) return;
    var order = prefs.map(function (p) { return p.key; });
    var visible = {};
    prefs.forEach(function (p) { visible[p.key] = !!p.visible; });

    var rows = table.querySelectorAll('tr');
    rows.forEach(function (row) {
      var cells = Array.prototype.slice.call(row.children);
      var dataCells = cells.filter(function (c) { return c.hasAttribute('data-col'); });
      if (!dataCells.length) return; // e.g. an empty-state row spanning the whole table
      var anchor = cells.filter(function (c) { return !c.hasAttribute('data-col'); })[0] || null;
      var byKey = {};
      dataCells.forEach(function (c) { byKey[c.getAttribute('data-col')] = c; });

      dataCells.forEach(function (c) {
        var key = c.getAttribute('data-col');
        c.style.display = (key in visible) ? (visible[key] ? '' : 'none') : '';
      });

      order.forEach(function (key) {
        var c = byKey[key];
        if (!c) return;
        if (anchor) { row.insertBefore(c, anchor); } else { row.appendChild(c); }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (window.LIST_COLUMNS_TABLE_ID && window.LIST_COLUMNS_PREFS) {
      applyListColumns(window.LIST_COLUMNS_TABLE_ID, window.LIST_COLUMNS_PREFS);
    }
  });
})();
