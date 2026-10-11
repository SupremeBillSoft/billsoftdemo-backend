# Sales & Marketing — Decision Record

The 16 open questions raised while reviewing BRD §6.4 (Sales & Marketing), the research behind each answer, the chosen solution and why, and where it is implemented. Research checked October 2026; GST rules change, so re-check the sources before go-live.

**Status legend:** ✅ implemented in `sales/` · 🧩 architecture in place, needs a business input · ⏭️ deliberately deferred to a later phase.

---

## Summary

| # | Question | Decision | Status |
|---|---|---|---|
| 1 | Quotation converts to SO or Invoice? | Both, from an **Accepted** quotation | ✅ |
| 2 | What is an "accepted" quotation? | Status lifecycle Draft → Sent → Accepted / Declined → Converted; Expired is derived from Valid Till | ✅ |
| 3 | Remaining qty when an SO goes partly via DC, partly invoiced directly | Each SO line is **fulfilled** by a DC *or* an order-based invoice, whichever comes first; DC-based invoices consume the DC, never the SO | ✅ |
| 4 | Amendment on SO / Invoice | SO: same number, revision no. + date, snapshot of the previous version, once anything is fulfilled. Invoice: no amendments — correct with a Credit Note (or cancel the IRN within 24 h) | ✅ |
| 5 | Who reduces stock — DC or Invoice? | Whoever physically moves the goods first: the DC if there is one, else the invoice. Non-billable DCs reduce stock too; returns bring it back | ✅ |
| 6 | Negative stock for sales | Allowed with a warning by default (BRD), switchable off per company; purchases / returns / transfers never go negative | ✅ |
| 7 | Returnable DCs and the legacy G/L/O types | BRD types (Order / Direct / Non-billable) + a **purpose** (job work, approval, repair, sample…) + returnable tracking with the s.143 due date | ✅ |
| 8 | Legacy invoice types vs BRD types | Business logic uses the BRD types; numbering uses `Invoice / <type> / <tax nature>` so legacy series (Labour, Scrap, RMSales…) keep working | ✅ 🧩 |
| 9 | Legacy fields (App value, Format DC No, Part Code Load, PWD, Removal Date, LUP No.) | Mapped — see §9; **PWD** still to confirm | ✅ 🧩 |
| 10 | Coupons, gift vouchers, schemes, loyalty | Phase 3 promotions engine; now: invoice discount (before/after tax) + free (BOGO) lines | ⏭️ |
| 11 | POS Billing | Separate module in Phase 3 reusing the pricing engine, stock ledger and invoice model | ⏭️ |
| 12 | Payment on the invoice | Capture only (amount received, mode, reference, advance set-off); receipts and ledger in the Payment In module | ✅ |
| 13 | E-Invoice / E-Way Bill provider | Provider-agnostic adapter + built-in sandbox now; production via a GSP, chosen by the business | ✅ 🧩 |
| 14 | When is an e-way bill mandatory? | Consignment value > ₹50,000 (state-specific intra-state limits configurable), always for inter-state job work; shown as a check before generating | ✅ |
| 15 | Does an SO take payments? | No "paid" status on an SO; an **advance** can be recorded and is set off on its invoice | ✅ |
| 16 | BRD copy-paste slips | Treated as typos — see §16 | ✅ |

---

## 1. Quotation → Sale Order or → Invoice?

**BRD conflict:** §6.4.2 intro says quotations convert to Sale Orders; the list section says "Convert to Invoice".

**Research.** Zoho Books lets an accepted estimate convert to *either* a Sales Order (businesses that track fulfilment) or straight to an Invoice; the conversion carries all lines, amounts and taxes. ([Zoho Books — Estimates](https://www.zoho.com/books/help/estimate/), [estimate statuses](https://www.zoho.com/ae/books/kb/invoices/estimates-invoices-status.html))

**Decision.** Support both, only from an **Accepted** quotation. A quotation can be converted once; it then becomes *Converted* and is locked.

**Why.** Manufacturing customers (the main market per BRD §2) need the SO stage for delivery scheduling and part-wise closing; small traders sell directly from a quote. Forcing one path would make the other user re-key the document.

**Implementation.** `POST /api/sales/quotations/{id}/convert-to-sales-order/`, `.../convert-to-invoice/` (with the godown to sell from). Both reuse the normal create path (`services.quotation_to_*_data`) so numbering, pricing and validation are identical to a hand-made document.

## 2. What makes a quotation "accepted"?

**Research.** Zoho uses Draft → Sent → Accepted / Declined, with Expired once past the expiry date; acceptance can come from the customer portal or be marked manually. (same sources)

**Decision.** `Draft → Sent → Accepted | Declined → Converted`. *Expired* is **derived** (Valid Till passed while Draft/Sent), not stored, so extending Valid Till revives it. Accepted / Declined / Converted are locked (BRD "not allow to modify after accepted"). No approval workflow — acceptance is the customer's decision, recorded by the user.

**Why.** A stored "Expired" status goes stale and needs a nightly job; derivation is always correct. Re-using the PR/PO approval workflow would wrongly model an internal approval.

**Implementation.** `Quotation.status`, `Quotation.is_expired()`, actions `mark-sent`, `accept` (refused if expired), `decline`.

## 3. Remaining quantity across DC and direct invoicing

**Research.** Odoo tracks *ordered*, *delivered* and *invoiced* quantity on every order line; partial deliveries and backorders are normal, and invoicing follows either ordered or delivered quantities. ([Odoo — invoicing policy](https://odoo.com/zh_CN/documentation/17.0/applications/sales/sales/invoicing/invoicing_policy.html))

**Decision.** Each SO line's quantity is **fulfilled** by whichever document moves the goods first: a Delivery Challan, or an order-based invoice with no DC. A DC-based invoice consumes the **challan's** quantity, never the order's again.
`pending = ordered − delivered(DC) − invoiced-directly − short-closed`. Invoiced quantity is reported separately (direct + via challans). Nothing is stored as a counter — all derived from the downstream lines, so cancelling a document automatically gives the quantity back.

**Why.** It mirrors the physical truth (goods leave once) and prevents the classic double-count where a DC and its invoice both reduce the order. Derived quantities can't drift the way stored counters do.

**Implementation.** `SaleOrderItem.delivered_qty / directly_invoiced_qty / fulfilled_qty / pending_qty / invoiced_qty`; caps enforced in `DeliveryChallanSerializer.validate` and `SalesInvoiceSerializer.validate`. Status `Pending → Completed ("Order Completed")` via `refresh_sale_order_status`; `Closed` by Close Order / Bulk Close / closing lines (short-close).

## 4. Amendments

**Research.** Oracle Order Management creates a new version with the same order number and a version suffix; Sage X3 keeps a revision number with full header and line history; ERPNext amends by cancelling and re-creating. All keep the original visible. ([Oracle](https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/26a/fauom/revise-sales-orders-you-already-submitted.html), [Sage X3](https://www.sagecity.com/support_communities/sage_erp_x3/b/sageerp_x3_product_support_blog/posts/revision-management-for-sales-orders), [ERPNext](https://docs.frappe.io/erpnext/v14/user/manual/en/selling/articles/amending-sales-order-after-submit))

**Decision.**
- **Sale Order:** free edits until anything is delivered or invoiced. After that every edit is an **amendment**: reason required, revision no. + amendment date, a JSON **snapshot** of the version before the change, same SO number (shown as "SO/0001 Rev 2"). A line can't go below what's been fulfilled and fulfilled lines can't be removed.
- **Invoice:** the BRD's "Amendment No./Date" on Direct Invoice is the **customer's PO amendment** being billed, so it is stored as a reference only. A posted invoice is never "amended": edit freely until it has an IRN, a return or a credit note; after that, correct it with a Credit Note (GST law has no concept of amending an issued invoice), or cancel the IRN within 24 h.

**Why.** Keeping the number avoids breaking references already printed on DCs and dispatch documents (the ERPNext cancel-and-recreate approach changes the number). Snapshots give a full audit trail without a second copy of every table.

**Implementation.** `SaleOrder.revision_no / amendment_date`, `SaleOrderRevision`, `services.record_amendment / check_amended_lines`, `SaleOrderSerializer.update`, `GET .../sale-orders/{id}/revisions/`. Lines are updated **in place** (ids preserved) so challans and invoices stay linked. Invoice locking: `SalesInvoiceViewSet._locked_reason`.

## 5. Who reduces stock — DC or Invoice?

**Research.** In TallyPrime the Delivery Note reduces stock and the later sales invoice is *linked* to it by tracking number, so stock isn't reduced twice. ([Tally — purchase to sale](https://tallysolutions.com/business-guides/purchase-to-sale-tallyprime-gst-inventory/))

**Decision.** Stock leaves on the **first physical movement**: the DC when there is one (all three DC types — goods leave the premises for job work too), otherwise the invoice. DC-based invoice lines never post stock (any godown sent is ignored). Proforma never posts stock. Sales Returns and job-work receipts post stock back in. Cancelling / deleting reverses exactly what the document posted.

**Why.** Stock must match what is physically in the godown; a non-billable job-work challan still empties the shelf. Double-posting is prevented structurally, not by user discipline.

**Implementation.** `services.post_challan_stock / post_invoice_stock / post_sales_return_stock / post_challan_return_stock`, new movement types `Sale`, `Sales Return`, `Delivery Challan`, `Challan Return` in `inventory`. Test `FulfilmentTests.test_partial_delivery_then_direct_invoice_for_the_rest` fails if a DC-based invoice ever deducts again (verified by mutation).

## 6. Negative stock

**Research.** Zoho Inventory's out-of-stock setting is a **warning only** — the user can proceed and stock goes negative. ([Zoho — out of stock warning](https://www.zoho.com/in/inventory/kb/general-overview/out-of-stock-warning.md))

**Decision.** Company Settings → `allow_negative_stock_on_sales` (default **on**, per BRD §6.4.5 3.1). When on, a sale that exceeds stock posts and returns `stockWarnings` for the screen to show; when off, it is refused. Purchases, purchase/sales returns, adjustments and transfers **never** go negative regardless.

**Why.** Billing ahead of stock entry is common at counters and shouldn't stop a sale, but the business must see it; some companies want it strict. Keeping receipts/transfers strict protects the ledger's integrity where a negative could only mean a data error.

**Implementation.** `inventory.services.replace_document_movements(..., allow_negative=True)` returns warnings instead of raising; tests `NegativeStockTests`.

## 7. Returnable DCs and legacy DC types (G / L / O)

**Research.** Goods sent for job work move on a **delivery challan** (Rule 55), not an invoice; no tax if inputs come back within **1 year** and capital goods within **3 years** (CGST Act s.143), otherwise the original despatch is deemed a supply; movements are reported in ITC-04. ([Masters India — job work](https://www.mastersindia.co/books/indian-gst-a-comprehensive-guide/job-work-and-supply-chain/), [TaxGuru — job work procedure](https://taxguru.in/goods-and-service-tax/jobwork-procedure-gst-practical.html), [ITC-04](https://www.knowyourgst.com/blog/gst-form-itc-04-job-work-supplies-109/))

**Decision.** Keep the BRD's three types (Order / Direct / Non-billable) and add a **purpose** on non-billable challans (Job work, Sale on approval, Repair/rework, Sample, Other). Job work is automatically **returnable** with a **return due date** (1 or 3 years by goods category). Goods coming back are recorded with *Receive back* (stock in, closes the challan when fully returned). An **overdue returnables** list flags quantities that have become taxable supplies. Legacy `DC/G Returnable / NonReturnable`, `DC/L` (labour) and `DC/O Rework` map to purpose + returnable + tax nature; numbering uses `DC / <type> / Returnable` so a separate returnable series is possible.

**Why.** Purpose is what GST actually cares about; it drives the return clock and future ITC-04 reporting. Without return tracking, unreturned job-work stock silently becomes a tax liability.

**Implementation.** `DeliveryChallan.purpose / returnable / goods_category / return_due_date`, `ChallanReturn`, actions `receive-back`, `overdue-returnables`, `compliance.rules.job_work_return_due`.
**Confirm:** whether legacy "L" and "O" mean Labour and Rework as assumed.

## 8. Legacy invoice types vs BRD types

**Decision.** Business logic uses the BRD's five types (Order, DC, Direct, Export, Proforma) plus a **tax nature** taken from the legacy sub-types (Sales, Labour, Scrap, Rejection, RMSales). Document numbers are issued from `Invoice / <type> / <tax nature>`, falling back to `Invoice / <type>` and then plain `Invoice` (the Document Numbering lookup added earlier). Proforma has its own `InvoiceProforma` series — it is not a tax invoice.

**Why.** The legacy DocTypes (DI / Regular / General) mix "how the invoice was made" with "which number series"; separating them keeps the rules clear while any legacy series can still be configured exactly. Document codes match the legacy `Doc_Number_Format` names (Invoice, InvoiceProforma, DC, CreditNote), so the 27 existing rows can be imported unchanged.

**Implementation.** `SalesInvoice.invoice_type / tax_nature`, `SalesInvoiceSerializer.numbering`, `docnumbering.services.DEFAULT_FORMATS`.
**Confirm:** what legacy *DI*, *Regular* and *General* meant, so their rows can be mapped onto the new types during data import.

## 9. Legacy field meanings

| Legacy field | Interpretation | Where |
|---|---|---|
| **App value** | *Approximate value* of goods on a challan — Rule 55 requires a value even with no invoice. Defaults to Σ qty × rate. | `DeliveryChallan.approx_value` |
| **Format DC No** | The formatted challan number (prefix/year/sequence) — no separate field needed; it *is* `dc_number`. | Document Numbering |
| **Part Code Load** | Load the customer's mapped items with *their* part codes (Party Master → Items → "Item code in print"). | `GET /api/sales/customer-items/?customer=` |
| **Removal Date** | Date & time goods are removed for supply; the tax invoice must be issued on or before it (CGST Act s.31). | `SalesInvoice.removal_datetime` |
| **LUP No.** | Read as **LUT No.** (Letter of Undertaking for zero-rated export without IGST). | `SalesInvoice.lut_number`, Company Settings `lut_number` |
| **Inward DC No / Date** | The customer's own DC reference for material they sent in (job work received / rework). | `DeliveryChallan.inward_dc_no / inward_dc_date` |
| **PWD** | **Unclear** — possibly "Packing / Weight / Dimension" or "Price with Discount". Not modelled. | 🧩 confirm |

## 10. Coupons, gift vouchers, free-item schemes, loyalty

**Research.** A discount recorded on the invoice at the time of supply reduces the taxable value; BOGO is treated as one supply of two items at a single price (full ITC kept); vouchers are taxed at issue if the goods are identifiable, otherwise at redemption. ([Suvit — GST on sales schemes](https://www.suvit.io/post/gst-on-sales-schemes), [Avalara — vouchers](https://www.avalara.com/in/en/blog/2020/03/gst-on-gift-vouchers-cards-and-coupons.html))

**Decision.** **Phase 3**, as a promotions engine with its own masters (Coupon, Gift Voucher, Scheme, Loyalty) — the BRD names the behaviour but defines no masters, limits or accounting. In scope **now**, because the invoice needs them anyway: invoice-level discount (% or amount, **before tax** = reduces taxable value / **after tax** = reduces payable) and **free lines** (`is_free`: zero value, stock still leaves) for BOGO.

**Why.** Each promotion type has distinct GST timing; building them without masters would hard-code rules. The two building blocks shipped now are what every scheme ultimately resolves to on the invoice.

**Implementation.** `pricing.price_document` (doc discount before/after tax, free lines). Phase 3 plan: `promotions` app → evaluates eligible schemes against the cart → emits discount / free lines into the same pricing engine; vouchers as liabilities in accounting.

## 11. POS Billing

**Decision.** Separate **Phase 3** module (`pos` app + touch UI): counters, shifts, cashier, barcode scan, hold/recall/suspend, split payments, loyalty. It **reuses** the pricing engine, stock ledger (`allow_negative`), and creates `SalesInvoice` records (B2C, so no IRN) — reports stay unified.

**Why.** POS has a different UX (keyboard/scanner speed, offline tolerance) and its own operational objects (shifts, cash drawer) but must not fork the tax and stock logic.

## 12. Payment captured on the invoice

**Decision.** Capture only: `amount_received`, `payment_mode`, `payment_reference`, `advance_adjusted` (from the SO), `balance_amount` (derived). Validation: received + advance can't exceed the total. Receipt vouchers, bank/cash ledger postings and allocation across invoices belong to **Payment In** (BRD §6.8.2), which will read these fields to create receipts.

**Why.** Posting to ledgers before the Chart of Accounts exists would mean migrating half-correct entries later; capture-only keeps the data without inventing accounting.

## 13. E-Invoice / E-Way Bill provider

**Research.** E-invoicing applies to B2B, export and credit/debit notes once aggregate turnover exceeds ₹5 crore — not to B2C. ([Vyapar](https://vyaparapp.in/blog/?p=1231), [SMFG](https://www.smfgindiacredit.com/knowledge-center/what-is-e-invoice-in-gst.aspx)) Six IRPs (NIC1/2, IRIS, Clear, EY, Cygnet) each offer a sandbox; access is through a GSP or direct API after a compliance audit. ([ClearTax — choosing a GSP](https://cleartax.in/s/gst-suvidha-provider-gsp-e-invoicing-api-access), [Masters India — integration modes](https://www.mastersindia.co/blog/e-invoicing-api-integration-modes/), [GSTN advisory on direct API](https://caclub.in/gstn-advisory-on-direct-api-integration-with-irps-for-e-invoicing-a-comprehensive-guide/)) IRN cancellation window is 24 h; from 1 Apr 2025 taxpayers with AATO ≥ ₹10 crore must report within 30 days. ([ClearTax — 30-day limit](https://cleartax.in/s/time-limit-for-reporting-e-invoices-on-the-irp-portal), [Taxmann](https://www.taxmann.com/post/blog/time-limit-of-30-days-for-reporting-e-invoice-on-irp-portal-for-taxpayers-with-aato-of-10-crores-and-above-gstn-update/))

**Decision.** A **provider-agnostic adapter**: `EInvoiceProvider` (generate/cancel IRN, generate/cancel EWB, update vehicle). A built-in **SandboxProvider** works today with no credentials — it computes the IRN with the real formula (SHA-256 of seller GSTIN + FY + doc type + doc number) and validates like the IRP. Production goes through a **GSP** (recommended over direct IRP API: no audit burden, one vendor for both e-invoice and e-way bill, managed token handling) — implement `GspProvider` against the chosen GSP's sandbox. The payload is the standard INV-01 v1.1 JSON, so switching GSP changes only the adapter.
Rules enforced: B2B/export only, never B2C or proforma; 30-day limit when `aato_10cr_or_more`; IRN cancel only within 24 h (after that: Credit Note); an e-invoiced document is locked.

**Why.** Avoids vendor lock-in and lets the whole flow be built and tested before a commercial contract exists.

**Implementation.** `sales/compliance/` (`rules.py`, `payload.py`, `providers.py`, `actions.py`), `settings.EINVOICE_PROVIDER` (`sandbox` | `gsp`, credentials from `EINVOICE_GSP_*` env vars), Company Settings `einvoice_enabled`, `aato_10cr_or_more`. Endpoints `.../invoices/{id}/einvoice/generate|cancel/`, `.../credit-notes/{id}/einvoice/...`.
**Confirm:** which GSP (business / commercial decision).

## 14. When is an e-way bill mandatory?

**Research.** Required when consignment value exceeds ₹50,000; several states (e.g. Maharashtra, Delhi, Tamil Nadu) set higher intra-state limits; inter-state job work needs one regardless of value; validity 1 day per 200 km (20 km for over-dimensional cargo); not for documents older than 180 days (from 1 Jan 2025); cancellable within 24 h. ([Bajaj Finserv](https://www.bajajfinserv.in/eway-bill-limit), [ClearTax](https://cleartax.in/s//eway-bill-gst-rules-compliance), [Busy — state thresholds](https://busy.in/gst/state-wise-threshold-limits-for-e-way-bills.md), [LegalDev](https://legaldev.in/blog/e-way-bill-rules-generation-guide))

**Decision.** `eway-bill-check` tells the user *whether and why* one is required (threshold from Company Settings, per-state intra-state overrides, inter-state job work). Generation is always allowed (voluntary is legal) from an invoice or a challan; validity is computed; Part-B vehicle updates are kept as history; cancel within 24 h; 180-day document age enforced. This also answers Party issue #20: the ship-to GSTIN check should key off this rule rather than be always-mandatory.

**Implementation.** `compliance.rules.eway_bill_*`, `compliance.actions.eway_bill_check / generate_eway_bill / cancel_eway_bill / update_vehicle`, `EWayBill` model, `/api/sales/eway-bills/`.

## 15. Does a Sale Order take payments?

**Research.** GST on advances received for **goods** is not payable (Notification 66/2017-CT); tax arises on the invoice. ([Tally — advances](https://help.tallysolutions.com/tally-prime/gst-reports/india-gst-advance-receipts-tally/))

**Decision.** The SO's "Payment Details" block (copied from the invoice) is replaced by an **advance**: `advance_received` + mode. It is set off automatically on the first invoice made from that single order (`advance_balance` tracks what's left). An SO never becomes "paid".

## 16. BRD copy-paste slips (treated as typos)

- Quotation list "follow-up fields optional when Closed" — copied from Enquiry; Quotation keeps an optional follow-up date only.
- SO "additional charges in the sales invoice" — applies to the SO as charges, implemented on every priced document.
- Credit Note list "Sale Return No." column — shown as the linked Sales Return (if any) next to the Invoice No.
- E-Way Bill create "IRP returns IRN / QR" — the e-way bill system returns **EWB No. and Valid Till**; implemented that way.
- Credit Note create screen titled "Sales Return Details Section" — it is the Credit Note details.

Also aligned with GST law: a **credit note must reference the original invoice** and be issued by **30 November** following the financial year of the supply (CGST Act s.34) — enforced, with the deadline shown on screen. ([Busy — credit notes](https://busy.in/gst/guide-to-a-credit-note-under-gst/), [TaxGuru](https://taxguru.in/goods-and-service-tax/adjustment-tax-liability-refund-gst-credit-note.html))

---

## Delivery plan

| Phase | Scope | State |
|---|---|---|
| 1 | Backend: Enquiry (+follow-ups), Quotation, Sale Order (+amendments, close), Delivery Challan (+returnable), Sales Invoice (5 types), Sales Return, Credit Note, pricing engine, stock rules, numbering | ✅ done — `sales/`, 46 tests |
| 1 | Compliance: e-invoice & e-way bill rules, payload, sandbox provider | ✅ done |
| 2 | Frontend screens for all of the above (reusing the purchase screens' patterns), print / share | next |
| 2 | Live GSP adapter once a provider is chosen | needs decision |
| 3 | POS Billing, promotions engine (coupons, vouchers, schemes, loyalty) | later |

## API map (`/api/sales/`)

`enquiries/` (+`follow-ups/`, `summary/`) · `quotations/` (+`mark-sent`, `accept`, `decline`, `convert-to-sales-order`, `convert-to-invoice`) · `sale-orders/` (+`pending-lines`, `revisions`, `close`, `close-lines`, `bulk-close`, `convert-to-invoice`, `bulk-convert-to-invoice`) · `delivery-challans/` (+`cancel`, `receive-back`, `invoiceable-lines`, `overdue-returnables`, `eway-bill-check`, `eway-bill`) · `invoices/` (+`cancel`, `returnable-lines`, `einvoice/generate`, `einvoice/cancel`, `eway-bill-check`, `eway-bill`) · `returns/` (+`cancel`) · `credit-notes/` (+`cancel`, `einvoice/...`) · `eway-bills/` (+`cancel`, `update-vehicle`) · `customer-items/?customer=`. List endpoints accept `?from=&to=&status=&customer=&search=`.
