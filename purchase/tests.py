# purchase/tests.py
#
# Covers the BRD-alignment rules for the purchase chain:
#   - PR without supplier, PO lines sourced per-PR-line with pending caps
#   - refund statuses (Return / Debit Note), Debit Note linked to an invoice
#   - per-line Tax Type, discount / P&F / freight, Freight invoices
#   - stock posting into godowns (invoice in, return out, cancel/delete)
#   - Company Settings state, approval rules, party eligibility

import datetime
from decimal import Decimal
from unittest import mock

from rest_framework.test import APITestCase

from docnumbering.models import DocumentNumberFormat
from inventory.models import Godown
from inventory.services import stock_on_hand
from items.models import Item
from parties.models import Party
from purchase.models import DebitNote, PurchaseOrderItem, PurchaseRequisition
from tax.models import Tax
from tenants.services import get_company_settings, get_current_tenant

TODAY = datetime.date(2026, 10, 6)
APPROVE = {"decision": "Approved", "approvedBy": "Manager", "reason": "Checked"}


class PurchaseFlowTestBase(APITestCase):
    def setUp(self):
        self.tenant = get_current_tenant(None)
        settings = get_company_settings(self.tenant)
        settings.state = "Karnataka"
        # Most tests build POs directly; the PO-from-PR rule has its own tests.
        settings.allow_po_without_pr = True
        settings.save()
        for doc in ["PR", "PO", "PI", "PRET", "DN"]:
            DocumentNumberFormat.objects.create(tenant=self.tenant, doc=doc, prefix=doc, prefix_separator="-",
                                                from_date=TODAY)
        self.tax18 = Tax.objects.create(tenant=self.tenant, tax_name="18", cgst_percentage=9,
                                        sgst_percentage=9, igst_percentage=18)
        self.tax5 = Tax.objects.create(tenant=self.tenant, tax_name="5", cgst_percentage=2.5,
                                       sgst_percentage=2.5, igst_percentage=5)
        self.supplier = Party.objects.create(tenant=self.tenant, party_code="SUP1", party_name="Supplier One",
                                             is_vendor=True, state="Karnataka")
        self.other_supplier = Party.objects.create(tenant=self.tenant, party_code="SUP2",
                                                   party_name="Supplier Two", is_vendor=True, state="Tamil Nadu")
        self.item_a = Item.objects.create(tenant=self.tenant, item_code="A", item_name="Item A", billing_uom="NOS",
                                          tax_type=str(self.tax18.id), purchase_price=100)
        self.item_b = Item.objects.create(tenant=self.tenant, item_code="B", item_name="Item B", billing_uom="NOS")
        self.freight = Item.objects.create(tenant=self.tenant, item_code="FRT", item_name="Freight",
                                           billing_uom="NOS", item_type="Service", tax_type=str(self.tax5.id))
        self.godown = Godown.objects.create(tenant=self.tenant, name="Main", is_main=True)
        self.godown2 = Godown.objects.create(tenant=self.tenant, name="Second")

    def decide(self, resource, doc_id, payload=APPROVE):
        return self.client.post(f"/api/{resource}/{doc_id}/decision/", payload, format="json")

    def create_approved_pr(self, supplier, lines):
        response = self.client.post("/api/purchase-requisitions/", {
            "supplier": supplier.id if supplier else None,
            "prDate": str(TODAY),
            "items": [{"item": item.id, "quantity": qty} for item, qty in lines],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        decision = self.decide("purchase-requisitions", response.json()["id"])
        self.assertEqual(decision.status_code, 200, decision.data)
        return PurchaseRequisition.objects.get(pk=response.json()["id"])

    def create_approved_po(self, supplier=None, lines=None):
        supplier = supplier or self.supplier
        lines = lines or [{"item": self.item_a.id, "orderQty": "10", "rate": "100"}]
        response = self.client.post("/api/purchase-orders/", {
            "supplier": supplier.id, "poDate": str(TODAY), "items": lines,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.decide("purchase-orders", response.json()["id"]).status_code, 200)
        return response.json()

    def invoice_payload(self, po, qty="10", godown=None, supplier_invoice_no="S-1"):
        line = po["items"][0]
        return {
            "invoiceType": "PO",
            "purchaseOrder": po["id"],
            "supplier": po["supplier"],
            "invoiceDate": str(TODAY),
            "supplierInvoiceNo": supplier_invoice_no,
            "supplierInvoiceDate": str(TODAY),
            "items": [{"poItem": line["id"], "item": line["item"], "invoicedQty": qty, "rate": line["rate"],
                       "godown": (godown or self.godown).id}],
        }

    def create_invoice(self, qty="10"):
        po = self.create_approved_po()
        response = self.client.post("/api/purchase-invoices/", self.invoice_payload(po, qty=qty), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.json()

    def stock(self, item=None, godown=None):
        return stock_on_hand(self.tenant, (item or self.item_a).id, (godown or self.godown).id)


class PurchaseRequisitionTests(PurchaseFlowTestBase):
    def test_pr_can_be_created_without_supplier_and_with_type(self):
        response = self.client.post("/api/purchase-requisitions/", {
            "prType": "General",
            "prDate": str(TODAY),
            "items": [{"item": self.item_a.id, "quantity": "5"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.json()["supplier"])
        self.assertEqual(response.json()["prType"], "General")

    def test_inactive_or_suspended_supplier_is_blocked(self):
        for status in ["Inactive", "Suspend/Hold"]:
            self.supplier.status = status
            self.supplier.save()
            response = self.client.post("/api/purchase-requisitions/", {
                "supplier": self.supplier.id, "prDate": str(TODAY),
                "items": [{"item": self.item_a.id, "quantity": "1"}],
            }, format="json")
            self.assertEqual(response.status_code, 400, status)


class ApprovalRuleTests(PurchaseFlowTestBase):
    def create_pending_pr(self):
        response = self.client.post("/api/purchase-requisitions/", {
            "prDate": str(TODAY), "items": [{"item": self.item_a.id, "quantity": "1"}],
        }, format="json")
        return response.json()["id"]

    def test_approved_by_and_reason_are_required(self):
        pr_id = self.create_pending_pr()
        self.assertEqual(self.decide("purchase-requisitions", pr_id, {"decision": "Approved"}).status_code, 400)
        self.assertEqual(self.decide("purchase-requisitions", pr_id,
                                     {"decision": "Approved", "approvedBy": "M", "reason": " "}).status_code, 400)
        approved = self.decide("purchase-requisitions", pr_id)
        self.assertEqual(approved.json()["approvedBy"], "Manager")

    def test_hold_can_be_released_to_pending(self):
        pr_id = self.create_pending_pr()
        self.decide("purchase-requisitions", pr_id, {**APPROVE, "decision": "Hold"})
        released = self.decide("purchase-requisitions", pr_id, {**APPROVE, "decision": "Pending"})
        self.assertEqual(released.status_code, 200, released.data)
        self.assertEqual(released.json()["status"], "Pending")
        self.assertEqual(released.json()["approvedBy"], "")
        self.assertEqual(self.decide("purchase-requisitions", pr_id).json()["status"], "Approved")

    def test_only_hold_can_be_released(self):
        pr_id = self.create_pending_pr()
        self.decide("purchase-requisitions", pr_id)
        response = self.decide("purchase-requisitions", pr_id, {**APPROVE, "decision": "Pending"})
        self.assertEqual(response.status_code, 400)

    def test_auto_approval_is_by_creator_role(self):
        settings = get_company_settings(self.tenant)
        settings.auto_approve_pr_roles = ["Manager"]
        settings.save()
        with mock.patch("purchase.views.current_user_role", return_value="Manager"):
            manager_pr = self.create_pending_pr()
        with mock.patch("purchase.views.current_user_role", return_value="User"):
            user_pr = self.create_pending_pr()
        self.assertEqual(PurchaseRequisition.objects.get(pk=manager_pr).status, "Approved")
        self.assertEqual(PurchaseRequisition.objects.get(pk=user_pr).status, "Pending")

    def test_nothing_auto_approves_without_a_logged_in_role(self):
        settings = get_company_settings(self.tenant)
        settings.auto_approve_pr_roles = ["Super Admin", "Admin", "Manager", "User"]
        settings.save()
        self.assertEqual(PurchaseRequisition.objects.get(pk=self.create_pending_pr()).status, "Pending")


class PurchaseOrderFromPRTests(PurchaseFlowTestBase):
    def po_payload(self, supplier, lines):
        return {
            "supplier": supplier.id,
            "poDate": str(TODAY),
            "items": [
                {"prItem": pr_item.id if pr_item else None, "item": item.id, "orderQty": qty, "rate": "10"}
                for pr_item, item, qty in lines
            ],
        }

    def test_one_po_can_draw_from_several_prs_including_one_without_supplier(self):
        pr1 = self.create_approved_pr(self.supplier, [(self.item_a, "10")])
        pr2 = self.create_approved_pr(None, [(self.item_b, "4")])
        response = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr1.items.get(), self.item_a, "10"),
            (pr2.items.get(), self.item_b, "4"),
        ]), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.json()["prNumbers"], sorted([pr1.pr_number, pr2.pr_number]))
        self.assertEqual(response.json()["items"][0]["prNumber"], pr1.pr_number)

    def test_pr_can_be_split_across_pos_and_qty_is_capped(self):
        pr = self.create_approved_pr(self.supplier, [(self.item_a, "10")])
        pr_item = pr.items.get()
        first = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr_item, self.item_a, "6")]), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        over = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr_item, self.item_a, "5")]), format="json")
        self.assertEqual(over.status_code, 400)
        remainder = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr_item, self.item_a, "4")]), format="json")
        self.assertEqual(remainder.status_code, 201, remainder.data)
        picker = self.client.get("/api/purchase-orders/approved-requisitions/", {"supplier": self.supplier.id})
        self.assertEqual(picker.json(), [])

    def test_editing_a_po_does_not_count_its_own_qty_against_the_cap(self):
        pr = self.create_approved_pr(self.supplier, [(self.item_a, "10")])
        payload = self.po_payload(self.supplier, [(pr.items.get(), self.item_a, "10")])
        created = self.client.post("/api/purchase-orders/", payload, format="json")
        resaved = self.client.patch(f"/api/purchase-orders/{created.json()['id']}/", payload, format="json")
        self.assertEqual(resaved.status_code, 200, resaved.data)

    def test_rejected_po_gives_pr_qty_back(self):
        pr = self.create_approved_pr(self.supplier, [(self.item_a, "10")])
        created = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr.items.get(), self.item_a, "10")]), format="json")
        self.decide("purchase-orders", created.json()["id"], {**APPROVE, "decision": "Rejected"})
        picker = self.client.get("/api/purchase-orders/approved-requisitions/", {"supplier": self.supplier.id})
        self.assertEqual(picker.json()[0]["lines"][0]["pendingOrderQty"], "10.000")

    def test_every_po_line_must_come_from_a_pr_by_default(self):
        settings = get_company_settings(self.tenant)
        settings.allow_po_without_pr = False
        settings.save()
        pr = self.create_approved_pr(self.supplier, [(self.item_a, "10")])
        standalone = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (None, self.item_a, "1")]), format="json")
        self.assertEqual(standalone.status_code, 400)
        mixed = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr.items.get(), self.item_a, "1"), (None, self.item_b, "1")]), format="json")
        self.assertEqual(mixed.status_code, 400)
        from_pr = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr.items.get(), self.item_a, "1")]), format="json")
        self.assertEqual(from_pr.status_code, 201, from_pr.data)

    def test_pr_for_another_supplier_is_rejected(self):
        pr = self.create_approved_pr(self.other_supplier, [(self.item_a, "10")])
        response = self.client.post("/api/purchase-orders/", self.po_payload(self.supplier, [
            (pr.items.get(), self.item_a, "1")]), format="json")
        self.assertEqual(response.status_code, 400)


class LineTaxAndChargesTests(PurchaseFlowTestBase):
    def test_tax_defaults_from_item_and_uses_cgst_sgst_intra_state(self):
        line = self.create_approved_po()["items"][0]
        self.assertEqual(line["tax"], self.tax18.id)
        self.assertEqual(line["cgst"], "90.00")
        self.assertEqual(line["sgst"], "90.00")

    def test_item_tax_saved_as_its_screen_label_is_recognised(self):
        # The Item / HSN screens store "{taxName}% {taxType}"; older rows
        # hold free text in the other order.
        for label in ["18% GST", "GST 18%", "gst 18 %"]:
            self.item_a.tax_type = label
            self.item_a.save()
            line = self.create_approved_po()["items"][0]
            self.assertEqual(line["tax"], self.tax18.id, label)

    def test_inter_state_uses_igst(self):
        po = self.create_approved_po(supplier=self.other_supplier)
        self.assertEqual(po["items"][0]["igst"], "180.00")

    def test_user_can_override_tax(self):
        po = self.create_approved_po(lines=[{"item": self.item_a.id, "orderQty": "10", "rate": "100",
                                             "tax": self.tax5.id}])
        self.assertEqual(po["items"][0]["taxName"], "5")
        self.assertEqual(po["items"][0]["cgst"], "25.00")

    def test_discount_pf_and_freight_are_taxed_at_line_rate(self):
        po = self.create_approved_po(lines=[{
            "item": self.item_a.id, "orderQty": "10", "rate": "100",
            "discountPercent": "10", "packForwardAmount": "50", "freightAmount": "50",
        }])
        line = po["items"][0]
        self.assertEqual(line["discountAmount"], "100.00")
        self.assertEqual(line["taxableValue"], "1000.00")  # 1000 - 100 + 50 + 50
        self.assertEqual(line["lineTotal"], "1180.00")

    def test_inactive_tax_is_rejected(self):
        self.tax5.status = "Inactive"
        self.tax5.save()
        response = self.client.post("/api/purchase-orders/", {
            "supplier": self.supplier.id, "poDate": str(TODAY),
            "items": [{"item": self.item_a.id, "orderQty": "1", "rate": "1", "tax": self.tax5.id}],
        }, format="json")
        self.assertEqual(response.status_code, 400)


class PurchaseInvoiceStockTests(PurchaseFlowTestBase):
    def test_invoice_receives_stock_into_godown(self):
        self.create_invoice(qty="10")
        self.assertEqual(self.stock(), Decimal("10"))

    def test_godown_is_required_for_products(self):
        payload = self.invoice_payload(self.create_approved_po())
        payload["items"][0]["godown"] = None
        self.assertEqual(self.client.post("/api/purchase-invoices/", payload, format="json").status_code, 400)

    def test_cannot_invoice_more_than_pending(self):
        po = self.create_approved_po()
        self.client.post("/api/purchase-invoices/", self.invoice_payload(po, qty="6"), format="json")
        over = self.client.post("/api/purchase-invoices/",
                                self.invoice_payload(po, qty="5", supplier_invoice_no="S-2"), format="json")
        self.assertEqual(over.status_code, 400)

    def test_cancel_then_delete_gives_back_po_qty_and_stock_once(self):
        invoice = self.create_invoice(qty="10")
        po_item = PurchaseOrderItem.objects.get(pk=invoice["items"][0]["poItem"])
        self.client.post(f"/api/purchase-invoices/{invoice['id']}/cancel/")
        self.assertEqual(self.stock(), Decimal("0"))
        self.client.delete(f"/api/purchase-invoices/{invoice['id']}/")
        po_item.refresh_from_db()
        self.assertEqual(po_item.invoiced_qty, Decimal("0"))
        self.assertEqual(self.stock(), Decimal("0"))

    def test_cancel_is_refused_once_stock_has_moved_on(self):
        invoice = self.create_invoice(qty="10")
        self.client.post("/api/inventory/transfers/", {
            "fromGodown": self.godown.id, "toGodown": self.godown2.id, "transferDate": str(TODAY),
            "items": [{"item": self.item_a.id, "quantity": "8"}],
        }, format="json")
        self.assertEqual(self.client.post(f"/api/purchase-invoices/{invoice['id']}/cancel/").status_code, 400)

    def test_freight_invoice_takes_services_at_their_own_rate_without_stock(self):
        response = self.client.post("/api/purchase-invoices/", {
            "invoiceType": "Freight", "supplier": self.supplier.id, "invoiceDate": str(TODAY),
            "supplierInvoiceNo": "F-1", "supplierInvoiceDate": str(TODAY),
            "items": [{"item": self.freight.id, "invoicedQty": "1", "rate": "1000"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.json()["items"][0]["taxName"], "5")
        self.assertEqual(response.json()["netAmount"], "1050.00")

    def test_freight_invoice_rejects_products(self):
        response = self.client.post("/api/purchase-invoices/", {
            "invoiceType": "Freight", "supplier": self.supplier.id, "invoiceDate": str(TODAY),
            "supplierInvoiceNo": "F-1", "supplierInvoiceDate": str(TODAY),
            "items": [{"item": self.item_a.id, "invoicedQty": "1", "rate": "1", "godown": self.godown.id}],
        }, format="json")
        self.assertEqual(response.status_code, 400)


class PurchaseReturnTests(PurchaseFlowTestBase):
    def return_payload(self, invoice, qty, amount_refunded="0"):
        line = invoice["items"][0]
        return {
            "purchaseInvoice": invoice["id"],
            "supplier": invoice["supplier"],
            "returnDate": str(TODAY),
            "amountRefunded": amount_refunded,
            "items": [{"invoiceItem": line["id"], "item": line["item"], "returnedQty": qty, "rate": "100"}],
        }

    def test_status_follows_amount_refunded(self):
        invoice = self.create_invoice()
        created = self.client.post("/api/purchase-returns/", self.return_payload(invoice, "2"), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.json()["status"], "Unpaid")
        self.assertEqual(created.json()["balanceAmount"], "236.00")  # 200 + 18% (tax mirrors the invoice line)

        url = f"/api/purchase-returns/{created.json()['id']}/"
        self.assertEqual(self.client.patch(url, {"amountRefunded": "36"}, format="json").json()["status"],
                         "Partially Refunded")
        self.assertEqual(self.client.patch(url, {"amountRefunded": "236"}, format="json").json()["status"],
                         "Refunded")
        self.assertEqual(self.client.patch(url, {"amountRefunded": "237"}, format="json").status_code, 400)

    def test_return_takes_stock_out_of_the_invoice_godown_and_delete_puts_it_back(self):
        invoice = self.create_invoice(qty="10")
        created = self.client.post("/api/purchase-returns/", self.return_payload(invoice, "4"), format="json")
        self.assertEqual(created.json()["items"][0]["godownName"], "Main")
        self.assertEqual(self.stock(), Decimal("6"))
        self.client.delete(f"/api/purchase-returns/{created.json()['id']}/")
        self.assertEqual(self.stock(), Decimal("10"))

    def test_return_needs_stock_in_the_godown(self):
        invoice = self.create_invoice(qty="10")
        self.client.post("/api/inventory/adjustments/", {
            "item": self.item_a.id, "godown": self.godown.id, "adjustmentType": "Reduce", "quantity": "9",
            "date": str(TODAY),
        }, format="json")
        response = self.client.post("/api/purchase-returns/", self.return_payload(invoice, "2"), format="json")
        self.assertEqual(response.status_code, 400)

    def test_saved_returns_reserve_quantity_immediately(self):
        invoice = self.create_invoice(qty="10")
        self.client.post("/api/purchase-returns/", self.return_payload(invoice, "8"), format="json")
        second = self.client.post("/api/purchase-returns/", self.return_payload(invoice, "3"), format="json")
        self.assertEqual(second.status_code, 400)


class DebitNoteTests(PurchaseFlowTestBase):
    def dn_payload(self, invoice=None, supplier=None):
        line = invoice["items"][0] if invoice else None
        return {
            "purchaseInvoice": invoice["id"] if invoice else None,
            "supplier": (supplier or self.supplier).id,
            "debitNoteDate": str(TODAY),
            "items": [{"invoiceItem": line["id"] if line else None, "item": self.item_a.id,
                       "quantity": "1", "rate": "100"}],
        }

    def test_debit_note_links_to_purchase_invoice_and_inherits_its_tax(self):
        invoice = self.create_invoice()
        created = self.client.post("/api/debit-notes/", self.dn_payload(invoice), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.json()["invoiceNumber"], invoice["invoiceNumber"])
        self.assertEqual(created.json()["items"][0]["tax"], self.tax18.id)

    def test_supplier_must_match_invoice(self):
        invoice = self.create_invoice()
        response = self.client.post("/api/debit-notes/", self.dn_payload(invoice, supplier=self.other_supplier),
                                    format="json")
        self.assertEqual(response.status_code, 400)

    def test_cancel_blocks_modify(self):
        created = self.client.post("/api/debit-notes/", self.dn_payload(), format="json")
        dn_url = f"/api/debit-notes/{created.json()['id']}/"
        self.assertEqual(self.client.post(f"{dn_url}cancel/").json()["status"], "Cancelled")
        self.assertEqual(self.client.patch(dn_url, {"remarks": "x"}, format="json").status_code, 400)
        self.assertEqual(DebitNote.objects.get().status, DebitNote.STATUS_CANCELLED)


class InventoryTests(PurchaseFlowTestBase):
    def adjust(self, kind, qty, godown=None):
        return self.client.post("/api/inventory/adjustments/", {
            "item": self.item_a.id, "godown": (godown or self.godown).id, "adjustmentType": kind,
            "quantity": qty, "date": str(TODAY), "remarks": "count",
        }, format="json")

    def test_adjustment_cannot_go_below_zero_and_keeps_history(self):
        self.assertEqual(self.adjust("Add", "5").status_code, 201)
        self.assertEqual(self.adjust("Reduce", "6").status_code, 400)
        reduced = self.adjust("Reduce", "2")
        self.assertEqual(reduced.json()["oldStock"], "5.000")
        self.assertEqual(reduced.json()["updatedStock"], "3.000")
        history = self.client.get("/api/inventory/adjustments/", {"item": self.item_a.id}).json()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["updatedBy"], "Unknown (login pending)")

    def test_transfer_moves_stock_and_checks_availability(self):
        self.adjust("Add", "5")
        too_much = self.client.post("/api/inventory/transfers/", {
            "fromGodown": self.godown.id, "toGodown": self.godown2.id, "transferDate": str(TODAY),
            "items": [{"item": self.item_a.id, "quantity": "6"}],
        }, format="json")
        self.assertEqual(too_much.status_code, 400)
        ok = self.client.post("/api/inventory/transfers/", {
            "fromGodown": self.godown.id, "toGodown": self.godown2.id, "transferDate": str(TODAY),
            "items": [{"item": self.item_a.id, "quantity": "2"}],
        }, format="json")
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(self.stock(), Decimal("3"))
        self.assertEqual(self.stock(godown=self.godown2), Decimal("2"))

    def test_stock_list_shows_quantity_and_value(self):
        self.adjust("Add", "5")
        rows = self.client.get("/api/inventory/stock/", {"godown": self.godown.id}).json()
        row = next(r for r in rows if r["item"] == self.item_a.id)
        self.assertEqual(row["stockQty"], "5.000")
        self.assertEqual(row["stockValue"], "500.00")

    def test_godown_with_stock_history_cannot_be_deleted(self):
        self.adjust("Add", "1")
        self.assertEqual(self.client.delete(f"/api/godowns/{self.godown.id}/").status_code, 400)


class CompanySettingsTests(PurchaseFlowTestBase):
    def test_gstin_required_when_registered_and_validated(self):
        self.assertEqual(self.client.patch("/api/company-settings/", {"gstRegistered": True},
                                           format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/company-settings/", {"gstRegistered": True, "gstin": "BAD"},
                                           format="json").status_code, 400)
        ok = self.client.patch("/api/company-settings/", {"gstRegistered": True, "gstin": "29abcde1234f1z5"},
                               format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.json()["gstin"], "29ABCDE1234F1Z5")

    def test_company_state_drives_intra_vs_inter_state(self):
        self.client.patch("/api/company-settings/", {"state": "Tamil Nadu"}, format="json")
        po = self.create_approved_po()  # supplier is in Karnataka
        self.assertEqual(po["items"][0]["igst"], "180.00")
        self.assertEqual(self.client.get("/api/item-gst-options/").json()["sellerState"], "Tamil Nadu")
