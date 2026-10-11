# Purchase Module — full backend setup (PR → PR Approval → PO → PO Approval → Purchase Invoice → Purchase Return → Debit Note)

This folder now contains **complete, ready-to-drop-in files** —
`models.py`, `serializers.py`, `views.py`, `urls.py` — not fragments to
hand-merge. Earlier deliveries gave you `*_addition.py` pieces to merge
into your existing `purchase` app file-by-file; those are gone now,
consolidated into these four files directly, because the hand-merge
approach was producing broken intermediate states (missing `urls.py`,
missing classes) that are easy to avoid by just replacing the files outright.

**If your `purchase` app currently has other, unrelated models/views/
serializers in it** (anything besides what earlier PR/PO deliveries gave
you), don't blindly overwrite — merge these four files' content into your
existing ones instead. If it only ever had what I've been sending you, a
straight replace is safe.

## What depends on what

```
docnumbering  (standalone app - already set up)
common/gst    (standalone app - already set up)
approvals     (standalone app - already set up)
      |
      v
purchase.PurchaseRequisition --(Approve)--> lines ordered by --> purchase.PurchaseOrder
  (supplier optional)                        (per PO line,               |
                                              many PRs per PO,    (Approve) + invoiced by
                                              PR split over POs)          v
                                                              purchase.PurchaseInvoice
                                                                  |               |
                                                  sourced by (return goods)   linked by (adjust payable)
                                                                  v               v
                                                purchase.PurchaseReturn     purchase.DebitNote
                                                (refund status)             (refund status / Cancelled)
```

Each arrow is enforced server-side in each serializer's `validate()`, not
just hidden in the UI.

## 1. Standalone apps (skip any step already done)

- `docnumbering/` — own app, own migration. Needs one Doc Number Format row
  per document code in your Document Number Format settings screen: `PR`,
  `PO`, `PI`, `PRET`, `DN`.
- `common/gst/` — pure functions + a calculation-preview endpoint, no
  migration.
- `approvals/` — own app, own migration.

```bash
python manage.py makemigrations docnumbering approvals
python manage.py migrate
```

## 2. Drop in the `purchase` app files

Copy `models.py`, `serializers.py`, `views.py`, `urls.py`, and
`gst_resolution.py` into your `purchase/` app, replacing (or merging into,
per the note above) what's there now.

Make sure your root `urls.py` includes it:

```python
path("api/", include("purchase.urls")),
```

And that `"approvals"` is in `INSTALLED_APPS` (needed by PR's and PO's
`ApprovalActionMixin` — Purchase Invoice, Purchase Return and Debit Note
have no approval step, see below).

**Items app import** — `models.py` and `serializers.py` both import
`from items.models import Item`. Adjust this one import if your items
app/model is named differently — everything else should work once that
resolves.

## 3. Migrate

```bash
python manage.py makemigrations purchase
python manage.py migrate
```

I'm not hand-writing this migration — your `purchase` app's existing
migration history isn't visible from here.

## 4. GST field resolution - now wired to your real schema

`purchase/gst_resolution.py` is resolved against your actual `Item`/`HSN`/
`Tax`/`Party`/`Branch` models. One real placeholder remains:

- **Seller state/branch**: PO/Invoice/Return/DebitNote don't carry a
  `branch` FK yet, so the resolver uses the tenant's first `Branch` row as
  "the seller" for every document. Fine for a single-branch tenant; once
  you add a `branch` FK + picker to these documents, swap
  `_resolve_seller_branch()` to use `document.branch` directly - the rest
  of the file doesn't change.

One assumption to confirm: `Item.tax_type` is tried first (item-level
override), falling back to the HSN row's `tax_type` (via `Item.hsn_code`)
when the item's own field is blank - both are CharFields storing a `Tax.id`
as a string. If your legacy app only ever used one of these two fields,
say which and the fallback branch can be dropped.

## 5. What each new document actually does

**Purchase Requisition** — supplier is optional (BRD 6.5.1.1.2), and each
PR has a type: `Item` ("Item based") or `General`. Each PR line exposes
`ordered_qty` / `pending_order_qty` - how much of it POs have taken so far
(a Rejected PO gives its quantity back).

**Purchase Order** — the PR link lives on each **line** (`pr_item`), not on
the PO header (BRD 6.5.2.1.2). One PO can order lines from several Approved
PRs, and one PR can be split across several POs; each PO line's qty is
capped at what's still pending on its PR line. A PR raised without a
supplier can be ordered on any supplier's PO; a PR raised for a supplier
only on that supplier's PO. Lines with no `pr_item` are manual additions.

**Purchase Invoice** — created only against an **Approved** PO.
No approval workflow of its own (flagged design choice — see the
docstring at the top of the Purchase Invoice section in `models.py`).
Supports partial invoicing: a PO with 100 units can be invoiced 40 now and
60 later, tracked via `PurchaseOrderItem.invoiced_qty`/`pending_qty`. The
**same supplier invoice number can't be booked twice** for one supplier
(GST compliance — enforced as a DB constraint). Modifying or deleting an
invoice is blocked once a Purchase Return or an active Debit Note exists
against it; "Cancel" (not Delete) is the normal teardown path and reverses
the PO quantity it had consumed.

**Line pricing (all documents)** — every line carries a **Tax Type** (a Tax
master row): it defaults from the source line (PO line → invoice line →
return / debit note line) or the item's own tax, and the user can change
it. Intra-state uses the Tax row's CGST%+SGST%, inter-state its IGST%,
decided by Company Settings State vs the supplier's State. PO and invoice
lines also take Discount %, Pack & Fwd and Freight: taxable value = qty ×
rate − discount + P&F + freight, all taxed at the line's rate.

**Freight invoice** — `invoice_type="Freight"`: a standalone freight bill
with no PO. Lines must be **Service** items and are taxed at each service's
own rate; services are never stocked.

**Stock** — PO-based invoice lines need a godown and post stock into it;
returns take stock back out (default: the godown it came into). Cancel /
delete / edit re-post the document's stock and are refused if that would
leave any godown below zero (e.g. the goods were already transferred out).

**Purchase Return** — created only against an **Active** (non-cancelled)
Purchase Invoice. No approval workflow (BRD 6.5.4): `status` is the refund
status — `Unpaid` / `Partially Refunded` / `Refunded` — derived on every
save from `amount_refunded` vs `net_amount` (refund can't exceed net).
`balance_amount` is exposed read-only. Every saved return reserves its
quantity immediately; returned qty is validated against what's still
returnable on the invoice line.

**Debit Note** — linked to an **Active** Purchase Invoice ("Link to
Invoice", BRD 6.5.5.2), or standalone when left blank. Picking an invoice
pre-fills its lines (each keeps `invoice_item`), still editable. Same
refund-status model as Purchase Return, plus `Cancelled` via
`POST /debit-notes/{id}/cancel/` (a Cancelled note can't be modified).

## 6. Supporting endpoints for the frontend pickers

- `GET /purchase-orders/{id}/pending-lines/` — PO's lines still open for
  invoicing (feeds the Create Invoice item grid).
- `GET /purchase-invoices/invoiceable-orders/` — Approved POs with
  something still pending (feeds the "source PO" dropdown).
- `GET /purchase-invoices/{id}/returnable-lines/` — invoice's lines still
  open for return (feeds the Create Return item grid).
- `GET /purchase-returns/returnable-invoices/` — Active invoices with
  something still returnable (feeds the "source invoice" dropdown).
- `GET /purchase-orders/approved-requisitions/?supplier=<id>` — Approved
  PRs for that supplier (plus PRs with no supplier) that still have qty to
  order, each with its open `lines` (feeds the Create PO "PR No." picker).
- `GET /debit-notes/linkable-invoices/?supplier=<id>` — Active invoices,
  optionally for one supplier (feeds the "Link to Invoice" dropdown). The
  Debit Note screen then reads `GET /purchase-invoices/{id}/` for the lines.

## 7. Route param naming (frontend)

The frontend's route params are `:prNo`, `:poNo`, `:invoiceNo`,
`:returnNo`, `:debitNoteNo` (not a generic `:id`) to match this project's
convention — see the frontend `README.md` for the exact `<Route>` list and
import block. These still carry the record's database id, not the
human-readable document number string.
