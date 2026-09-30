# Mixed-flavor combo cases — design notes (not yet built)

Status: **parked / planning only** — nothing in this doc is implemented. Written
2026-09-30 after a design discussion; pick this back up when the team is ready
to build it.

## The ask

A few product categories (Coffee, Glucon, Sport & Electrolyte) are sold by the
case, and each flavor within a category is its own `Products` row (its own
case size/price/stock). The team wants to be able to combine different
flavors into one case and sell that as a single option to the customer.

Confirmed with the business (2026-09-30):
- Need **both** fixed, company-defined combos (a standing "recipe") **and**
  customer/salesperson-picked custom mixes per order.
- The case stays the atomic unit — no need to track individual
  pieces/sachets/bottles inside a case. `Products` has no "units per case"
  field today and none is needed for this feature.

## Key existing-architecture facts this design leans on

- `Products` is flat: one row = one flavor = one case, with its own
  `CostPrice`/`SellingPrice`/`MinStock`/`MaxStock`/`HSNCode`/`GSTRate`. No
  case-size or bill-of-materials concept exists anywhere in the schema.
- `SalesLines` and `StockIssueLines` are both one-row-per-real-`ProductID`,
  each with its own GST snapshot (`HSNCode`/`GSTRate`/`CGST`/`SGST`/`IGST`)
  and, for Stock Issues, its own reconciliation fields (`QtySold`,
  `QtyReturned`, `QtyFree`, `DiscountAmount`, `SchemeClaimAmount`,
  `LineComments`) filled in independently per line.
- These categories are sold primarily through the Stock Issue flow
  (`stock_issue_form` / `stock_issue_add_lines` → `stock_issue_reconcile`),
  not directly through Sales.

## Recommended design: a combo is a recipe/template, not a new inventory item

Don't give a combo its own stock, its own `ProductID` that carries real
inventory, or its own persisted invoice/issue line. Make it a saved recipe
that **expands into the real flavor lines that already exist** — the combo
never persists as a "thing" beyond the moment it's added to a Sale or Stock
Issue.

1. Two new tables:
   - `ComboDefinitions` (ComboID, Name, Category, Active)
   - `ComboDefinitionLines` (ComboID, ComponentProductID, QtyCases)

   Pure recipe data, e.g. "Coffee Variety Pack = 2 cases Original + 1 case
   Mocha + 1 case Hazelnut."

2. On the Stock Issue and Sale line-entry screens, add an "Add Combo" option
   next to "Add Product." Picking a combo appends its component lines (real
   flavor `ProductID`s, pre-filled quantities) into the line table — exactly
   as if each flavor had been added by hand. Every line stays individually
   editable/removable right there.

3. That's it. Nothing else changes.

### Why this satisfies both requirements

- **Fixed combos**: defined once as a `ComboDefinition`, reusable ("Combo A,"
  "Combo B," ...).
- **Custom mixes**: already fully supported today with zero new code — a
  salesperson can already add several different flavor `Products` to one
  Sale or Stock Issue. Don't use a combo template (or start from one and
  edit it) and you get a custom mix for free. The flexible case was never
  actually blocked by the current system.

### Why this is the safe choice for inventory/sales control

Every downstream system already operates per real `ProductID` line, so none
of it needs to change:
- Stock deduction, `MinStock`/`ReorderQty` alerts, and Stock Issue
  capacity/crediting logic all keep hitting the real flavor's own stock — a
  combo never has "its own inventory" to get out of sync with reality.
- GST: each flavor's `HSNCode`/`GSTRate` is already snapshotted per line at
  sale time, so even a combo whose flavors carry different GST rates is
  handled correctly automatically.
- Reconciliation: a salesperson can sell 2 cases of Mocha from a combo but
  return 1 unsold case of Original, because they're separate real lines —
  representable exactly as it happens on the ground. A single merged
  "combo" line couldn't capture that at all, so this isn't just simpler to
  build, it's the more correct model of reality.
- Reports, incentives, targets, scheme claims — all already
  product-line-based, so a combo just shows up as its real components in
  every existing report, with no special-casing anywhere.

### Optional, not required for v1

A nullable `ComboID` tag on `SalesLines`/`StockIssueLines` would let the
printed invoice/Stock Issue view visually group "these 3 lines came from
Combo A," purely cosmetic and never touched by any deduction/GST/report
logic. Skip for the first version; only add if it turns out to matter in
practice.

## Implementation scope, when this is picked up

1. Schema: `ComboDefinitions` + `ComboDefinitionLines`, plus a
   `migrate_*.py` script following the existing pattern.
2. A small Admin/Manager-only "Combos" settings page (CRUD) — same shape as
   the existing Custom Fields settings page.
3. UI/JS on the Stock Issue add-lines screen (build this one first, since
   that's where these categories are actually sold today) and the Sale
   form: an "Add Combo" picker that expands into normal line rows.
4. Tests: combo expansion adds correct lines/quantities, stock deducts from
   real components correctly, and reconciliation on a combo-sourced Stock
   Issue behaves identically to a hand-built one.

Open question for whoever resumes this: start with Stock Issue only, or
both screens together?
