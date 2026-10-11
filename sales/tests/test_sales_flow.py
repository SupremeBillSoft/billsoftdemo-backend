# sales/tests/test_sales_flow.py
#
# One test (or a few) per decision in the Sales decision record.

import datetime
from decimal import Decimal
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from inventory.models import Godown
from inventory.services import adjust_stock, stock_on_hand
from items.models import Item
from parties.models import Party
from sales.compliance import rules
from sales.models import EWayBill, Quotation, SaleOrder, SaleOrderRevision, SalesInvoice
from tax.models import Tax
from tenants.services import get_company_settings, get_current_tenant

TODAY = datetime.date.today()
API = "/api/sales"


class SalesTestBase(APITestCase):
    def setUp(self):
        self.tenant = get_current_tenant(None)
        settings = get_company_settings(self.tenant)
        settings.state = "Karnataka"
        settings.gstin = "29ABCDE1234F1Z5"
        settings.business_name = "Supreme Test Co"
        settings.pincode = "560001"
        settings.save()
        self.tax18 = Tax.objects.create(tenant=self.tenant, tax_name="18", cgst_percentage=9, sgst_percentage=9,
                                        igst_percentage=18)
        self.customer = Party.objects.create(tenant=self.tenant, party_code="C1", party_name="Local Customer",
                                             is_customer=True, state="Karnataka", gst_no="29AAAAA1111A1Z5",
                                             pincode="560002")
        self.outstation = Party.objects.create(tenant=self.tenant, party_code="C2", party_name="TN Customer",
                                               is_customer=True, state="Tamil Nadu", gst_no="33BBBBB2222B1Z5")
        self.b2c = Party.objects.create(tenant=self.tenant, party_code="C3", party_name="Walk-in", is_customer=True,
                                        state="Karnataka")
        self.exporter = Party.objects.create(tenant=self.tenant, party_code="E1", party_name="US Buyer",
                                             is_export_customer=True, state="", country="USA")
        self.item = Item.objects.create(tenant=self.tenant, item_code="P1", item_name="Pump", billing_uom="NOS",
                                        tax_type="18% GST", purchase_price=500)
        self.service = Item.objects.create(tenant=self.tenant, item_code="S1", item_name="Installation",
                                           billing_uom="NOS", item_type="Service", tax_type="18% GST")
        self.godown = Godown.objects.create(tenant=self.tenant, name="Main", is_main=True)
        adjust_stock(self.tenant, item=self.item, godown=self.godown, add=True, quantity=Decimal("100"),
                     movement_date=TODAY)

    # -- helpers --------------------------------------------------------
    def stock(self):
        return stock_on_hand(self.tenant, self.item.id, self.godown.id)

    def post(self, path, data, expect=201):
        response = self.client.post(f"{API}/{path}", data, format="json")
        self.assertEqual(response.status_code, expect, response.content[:600])
        return response.json() if response.content else None

    def patch(self, path, data, expect=200):
        response = self.client.patch(f"{API}/{path}", data, format="json")
        self.assertEqual(response.status_code, expect, response.content[:600])
        return response.json()

    def line(self, qty="10", rate="1000", **extra):
        return {"item": self.item.id, "quantity": qty, "rate": rate, **extra}

    def sale_order(self, qty="10", customer=None, **extra):
        return self.post("sale-orders/", {"customer": (customer or self.customer).id, "soDate": str(TODAY),
                                          "items": [self.line(qty)], **extra})

    def invoice(self, items, invoice_type="Direct", customer=None, expect=201, **extra):
        return self.post("invoices/", {"customer": (customer or self.customer).id, "invoiceDate": str(TODAY),
                                       "invoiceType": invoice_type, "items": items, **extra}, expect=expect)


class PricingTests(SalesTestBase):
    def test_intra_state_tax_defaults_from_item(self):
        so = self.sale_order()
        line = so["items"][0]
        self.assertEqual((line["taxName"], line["cgst"], line["sgst"]), ("18", "900.00", "900.00"))
        self.assertEqual(so["netAmount"], "11800.00")

    def test_inter_state_uses_igst(self):
        so = self.sale_order(customer=self.outstation)
        self.assertEqual(so["items"][0]["igst"], "1800.00")

    def test_tax_inclusive_rates(self):
        so = self.sale_order(qty="1", priceIncludesTax=True, items=[self.line("1", "1180")])
        self.assertEqual(so["items"][0]["taxableValue"], "1000.00")
        self.assertEqual(so["netAmount"], "1180.00")

    def test_document_discount_before_tax_reduces_taxable_value(self):
        so = self.sale_order(docDiscountType="Percent", docDiscountValue="10", docDiscountBeforeTax=True)
        self.assertEqual(so["totalTaxable"], "9000.00")
        self.assertEqual(so["netAmount"], "10620.00")

    def test_document_discount_after_tax_keeps_gst(self):
        so = self.sale_order(docDiscountType="Amount", docDiscountValue="500", docDiscountBeforeTax=False)
        self.assertEqual(so["totalTaxable"], "10000.00")
        self.assertEqual(so["netAmount"], "11300.00")

    def test_line_discount_percent_and_amount_derive_each_other(self):
        so = self.post("sale-orders/", {"customer": self.customer.id, "soDate": str(TODAY),
                                        "items": [self.line(discountAmount="1000")]})
        self.assertEqual(so["items"][0]["discountPercent"], "10.00")

    def test_additional_charge_carries_its_own_tax(self):
        so = self.sale_order(charges=[{"name": "Transport", "amount": "1000", "tax": self.tax18.id}])
        self.assertEqual(so["totalCharges"], "1180.00")
        self.assertEqual(so["netAmount"], "12980.00")

    def test_round_off(self):
        so = self.sale_order(qty="1", items=[self.line("1", "99.55")])
        self.assertEqual(so["netAmount"], "117.00")  # 117.469 -> 117
        self.assertEqual(so["roundOffAmount"], "-0.47")


class EnquiryTests(SalesTestBase):
    def payload(self, **extra):
        return {"enquiryDate": str(TODAY), "customerName": "Prospect Ltd", "contactNumber": "9876543210",
                "nextFollowUpDate": str(TODAY + datetime.timedelta(days=2)), **extra}

    def test_open_enquiry_needs_a_follow_up_and_no_past_dates(self):
        self.post("enquiries/", self.payload(nextFollowUpDate=None), expect=400)
        self.post("enquiries/", self.payload(nextFollowUpDate=str(TODAY - datetime.timedelta(days=1))), expect=400)
        created = self.post("enquiries/", self.payload())
        self.assertTrue(created["enquiryNumber"].startswith("ENQ/"))
        self.assertEqual(created["followUpState"], "upcoming")

    def test_feasibility_no_closes_the_enquiry(self):
        created = self.post("enquiries/", self.payload(feasibility=False, nextFollowUpDate=None))
        self.assertEqual(created["status"], "Closed")

    def test_multiple_follow_ups(self):
        created = self.post("enquiries/", self.payload())
        updated = self.post(f"enquiries/{created['id']}/follow-ups/", {"followUpDate": str(TODAY),
                                                                         "notes": "Call back"})
        self.assertEqual(len(updated["followUps"]), 2)
        self.assertEqual(updated["followUpState"], "today")

    def test_contact_number_numeric(self):
        self.post("enquiries/", self.payload(contactNumber="98-765"), expect=400)


class QuotationTests(SalesTestBase):
    def quotation(self):
        return self.post("quotations/", {"customer": self.customer.id, "quotationDate": str(TODAY),
                                         "validTill": str(TODAY + datetime.timedelta(days=30)),
                                         "items": [self.line("5")]})

    def test_accepted_quotation_is_locked_and_converts_to_sale_order(self):
        quote = self.quotation()
        self.post(f"quotations/{quote['id']}/convert-to-sales-order/", {}, expect=400)  # not accepted yet
        self.post(f"quotations/{quote['id']}/accept/", {}, expect=200)
        self.patch(f"quotations/{quote['id']}/", {"remarks": "x"}, expect=400)
        so = self.post(f"quotations/{quote['id']}/convert-to-sales-order/", {})
        self.assertEqual(so["quotationNumber"], quote["quotationNumber"])
        self.assertEqual(Quotation.objects.get(pk=quote["id"]).status, Quotation.STATUS_CONVERTED)
        # The order can still be edited with its (now Converted) quotation link
        # echoed back, as the edit screen does; a second order can't reuse it.
        self.patch(f"sale-orders/{so['id']}/", {"quotation": quote["id"], "remarks": "edited"})
        self.post("sale-orders/", {"customer": self.customer.id, "soDate": str(TODAY), "quotation": quote["id"],
                                   "items": [self.line("1")]}, expect=400)

    def test_accepted_quotation_can_also_be_invoiced_directly(self):
        quote = self.quotation()
        self.post(f"quotations/{quote['id']}/accept/", {}, expect=200)
        invoice = self.post(f"quotations/{quote['id']}/convert-to-invoice/", {"godown": self.godown.id})
        self.assertEqual(invoice["invoiceType"], "Direct")
        self.assertEqual(self.stock(), Decimal("95"))

    def test_expired_quotation_cannot_be_accepted(self):
        quote = self.post("quotations/", {"customer": self.customer.id, "quotationDate": str(TODAY),
                                          "validTill": str(TODAY - datetime.timedelta(days=1)),
                                          "items": [self.line()]})
        self.assertTrue(quote["isExpired"])
        self.post(f"quotations/{quote['id']}/accept/", {}, expect=400)


class FulfilmentTests(SalesTestBase):
    """Q3 + Q5: SO quantity is fulfilled by a DC or an order-based invoice;
    stock moves once - on the DC if there is one, else on the invoice."""

    def dc(self, so, qty, expect=201):
        line = so["items"][0]
        return self.post("delivery-challans/", {
            "customer": self.customer.id, "dcDate": str(TODAY), "dcType": "Order", "saleOrder": so["id"],
            "items": [{"soItem": line["id"], "item": self.item.id, "quantity": qty, "rate": "1000",
                       "godown": self.godown.id}]}, expect=expect)

    def test_partial_delivery_then_direct_invoice_for_the_rest(self):
        so = self.sale_order("10")
        challan = self.dc(so, "6")
        self.assertEqual(self.stock(), Decimal("94"))
        self.dc(so, "5", expect=400)  # only 4 pending

        # DC-based invoice: bills the challan, does NOT move stock again.
        dc_line = challan["items"][0]
        # (a godown sent on a challan line is ignored - the DC already moved the stock)
        dc_invoice = self.invoice([{"dcItem": dc_line["id"], "item": self.item.id, "quantity": "6", "rate": "1000",
                                    "godown": self.godown.id}], invoice_type="DC", deliveryChallans=[challan["id"]])
        self.assertEqual(self.stock(), Decimal("94"))
        self.assertEqual(dc_invoice["dcNumbers"], [challan["dcNumber"]])

        # Order-based invoice for the remaining 4: moves stock itself.
        so_line = so["items"][0]
        self.invoice([{"soItem": so_line["id"], **self.line("5"), "godown": self.godown.id}], invoice_type="Order",
                     saleOrder=so["id"], expect=400)
        self.invoice([{"soItem": so_line["id"], **self.line("4"), "godown": self.godown.id}], invoice_type="Order",
                     saleOrder=so["id"])
        self.assertEqual(self.stock(), Decimal("90"))
        order = SaleOrder.objects.get(pk=so["id"])
        self.assertEqual(order.status, SaleOrder.STATUS_COMPLETED)
        self.assertEqual(order.items.get().invoiced_qty, Decimal("10"))

    def test_cancelling_an_invoice_returns_stock_and_order_quantity(self):
        so = self.sale_order("10")
        inv = self.post(f"sale-orders/{so['id']}/convert-to-invoice/", {"godown": self.godown.id})
        self.assertEqual(self.stock(), Decimal("90"))
        self.post(f"invoices/{inv['id']}/cancel/", {"reason": "wrong rate"}, expect=200)
        self.assertEqual(self.stock(), Decimal("100"))
        self.assertEqual(SaleOrder.objects.get(pk=so["id"]).items.get().pending_qty, Decimal("10"))

    def test_proforma_moves_no_stock_and_reserves_no_quantity(self):
        so = self.sale_order("10")
        so_line = so["items"][0]
        pro = self.invoice([{"soItem": so_line["id"], **self.line("10")}], invoice_type="Proforma")
        self.assertTrue(pro["invoiceNumber"].startswith("PRO/"))
        self.assertEqual(self.stock(), Decimal("100"))
        self.assertEqual(SaleOrder.objects.get(pk=so["id"]).items.get().pending_qty, Decimal("10"))

    def test_bulk_convert_makes_one_invoice_per_customer(self):
        a = self.sale_order("2")
        b = self.sale_order("3")
        c = self.sale_order("1", customer=self.outstation)
        result = self.post("sale-orders/bulk-convert-to-invoice/", {"ids": [a["id"], b["id"], c["id"]],
                                                                     "godown": self.godown.id})
        self.assertEqual(len(result["invoices"]), 2)
        self.assertEqual(self.stock(), Decimal("94"))

    def test_close_order_and_close_lines(self):
        so = self.sale_order("10")
        self.dc(so, "4")
        closed = self.post(f"sale-orders/{so['id']}/close/", {"reason": "customer cancelled"}, expect=200)
        self.assertEqual(closed["status"], "Closed")
        self.assertEqual(closed["items"][0]["pendingQty"], "0.000")


class NegativeStockTests(SalesTestBase):
    """Q6: sales may go negative with a warning; configurable."""

    def test_sales_warn_but_post_when_allowed(self):
        inv = self.invoice([{**self.line("150"), "godown": self.godown.id}])
        self.assertEqual(self.stock(), Decimal("-50"))
        self.assertTrue(inv["stockWarnings"])

    def test_sales_blocked_when_setting_off(self):
        settings = get_company_settings(self.tenant)
        settings.allow_negative_stock_on_sales = False
        settings.save()
        self.invoice([{**self.line("150"), "godown": self.godown.id}], expect=400)


class AmendmentTests(SalesTestBase):
    """Q4: edits after fulfilment are amendments - same number, new revision."""

    def test_amendment_after_delivery(self):
        so = self.sale_order("10")
        line = so["items"][0]
        self.post("delivery-challans/", {
            "customer": self.customer.id, "dcDate": str(TODAY), "dcType": "Order", "saleOrder": so["id"],
            "items": [{"soItem": line["id"], "item": self.item.id, "quantity": "6", "godown": self.godown.id}]})
        edit = {"items": [{"id": line["id"], "item": self.item.id, "quantity": "12", "rate": "950"}]}
        self.patch(f"sale-orders/{so['id']}/", edit, expect=400)  # reason required
        amended = self.patch(f"sale-orders/{so['id']}/", {**edit, "amendmentReason": "Customer PO revised"})
        self.assertEqual((amended["revisionNo"], amended["soNumber"]), (1, so["soNumber"]))
        revision = SaleOrderRevision.objects.get()
        self.assertEqual(revision.snapshot["items"][0]["rate"], "1000.00")  # the version before the change
        too_low = {"items": [{"id": line["id"], "item": self.item.id, "quantity": "5", "rate": "950"}],
                   "amendmentReason": "x"}
        self.patch(f"sale-orders/{so['id']}/", too_low, expect=400)

    def test_edits_before_fulfilment_are_plain_edits(self):
        so = self.sale_order("10")
        edited = self.patch(f"sale-orders/{so['id']}/", {"remarks": "urgent"})
        self.assertEqual(edited["revisionNo"], 0)


class ChallanTests(SalesTestBase):
    """Q7: non-billable / returnable challans (Rule 55, s.143)."""

    def job_work_dc(self):
        return self.post("delivery-challans/", {
            "customer": self.customer.id, "dcDate": str(TODAY), "dcType": "NonBillable", "purpose": "JobWork",
            "goodsCategory": "Inputs",
            "items": [{"item": self.item.id, "quantity": "20", "rate": "500", "godown": self.godown.id}]})

    def test_non_billable_needs_a_purpose(self):
        self.post("delivery-challans/", {"customer": self.customer.id, "dcDate": str(TODAY), "dcType": "NonBillable",
                                         "items": [{"item": self.item.id, "quantity": "1", "godown": self.godown.id}]},
                  expect=400)

    def test_job_work_is_returnable_within_a_year_and_comes_back_into_stock(self):
        challan = self.job_work_dc()
        self.assertTrue(challan["returnable"])
        self.assertEqual(challan["returnDueDate"], str(rules.job_work_return_due(sent_on=TODAY, capital_goods=False)))
        self.assertEqual(challan["approxValue"], "10000.00")
        self.assertEqual(self.stock(), Decimal("80"))
        back = self.post(f"delivery-challans/{challan['id']}/receive-back/", {
            "returnDate": str(TODAY), "items": [{"dcItem": challan["items"][0]["id"], "quantity": "20"}]})
        self.assertEqual(back["returnStatus"], "Returned")
        self.assertEqual(self.stock(), Decimal("100"))

    def test_non_billable_challan_cannot_be_invoiced(self):
        challan = self.job_work_dc()
        self.invoice([{"dcItem": challan["items"][0]["id"], "item": self.item.id, "quantity": "1", "rate": "1"}],
                     invoice_type="DC", deliveryChallans=[challan["id"]], expect=400)


class ExportTests(SalesTestBase):
    def test_export_under_lut_is_zero_rated_and_needs_a_lut(self):
        items = [{**self.line("1"), "godown": self.godown.id}]
        self.invoice(items, invoice_type="Export", customer=self.exporter, expect=400)  # no LUT
        settings = get_company_settings(self.tenant)
        settings.lut_number = "AD290326000123X"
        settings.save()
        inv = self.invoice(items, invoice_type="Export", customer=self.exporter, currencyCode="USD")
        self.assertEqual((inv["totalIgst"], inv["lutNumber"]), ("0.00", "AD290326000123X"))

    def test_export_with_igst_is_inter_state(self):
        inv = self.invoice([{**self.line("1"), "godown": self.godown.id}], invoice_type="Export",
                           customer=self.exporter, exportWithIgst=True)
        self.assertEqual(inv["totalIgst"], "180.00")

    def test_export_needs_an_export_customer(self):
        self.invoice([{**self.line("1"), "godown": self.godown.id}], invoice_type="Export", expect=400,
                     exportWithIgst=True)


class EInvoiceTests(SalesTestBase):
    """Q13: e-invoice via the provider interface (sandbox here)."""

    def setUp(self):
        super().setUp()
        settings = get_company_settings(self.tenant)
        settings.einvoice_enabled = True
        settings.save()

    def test_b2b_invoice_gets_an_irn_and_is_then_locked(self):
        inv = self.invoice([{**self.line("1"), "godown": self.godown.id}])
        self.assertEqual(inv["einvoiceStatus"], "Pending")
        done = self.post(f"invoices/{inv['id']}/einvoice/generate/", {}, expect=200)
        self.assertEqual(len(done["irn"]), 64)
        self.assertEqual(done["einvoiceStatus"], "Generated")
        self.patch(f"invoices/{inv['id']}/", {"remarks": "x"}, expect=400)
        self.post(f"invoices/{inv['id']}/cancel/", {}, expect=400)  # cancel IRN first

    def test_b2c_and_proforma_are_not_e_invoiced(self):
        b2c = self.invoice([{**self.line("1"), "godown": self.godown.id}], customer=self.b2c)
        self.assertEqual(b2c["einvoiceStatus"], "NotApplicable")
        pro = self.invoice([self.line("1")], invoice_type="Proforma")
        self.assertEqual(pro["einvoiceStatus"], "NotApplicable")

    def test_irn_cancel_window_is_24_hours(self):
        inv = self.invoice([{**self.line("1"), "godown": self.godown.id}])
        self.post(f"invoices/{inv['id']}/einvoice/generate/", {}, expect=200)
        later = timezone.now() + datetime.timedelta(hours=25)
        with mock.patch("sales.compliance.actions.timezone.now", return_value=later):
            self.post(f"invoices/{inv['id']}/einvoice/cancel/", {"remark": "typo"}, expect=400)
        self.post(f"invoices/{inv['id']}/einvoice/cancel/", {"remark": "typo"}, expect=200)
        self.post(f"invoices/{inv['id']}/cancel/", {}, expect=200)

    def test_thirty_day_reporting_limit_for_large_taxpayers(self):
        settings = get_company_settings(self.tenant)
        settings.aato_10cr_or_more = True
        settings.save()
        old = self.invoice([{**self.line("1"), "godown": self.godown.id}],
                           invoiceDate=str(TODAY - datetime.timedelta(days=31)))
        self.post(f"invoices/{old['id']}/einvoice/generate/", {}, expect=400)


class EWayBillTests(SalesTestBase):
    """Q14: e-way bill rules."""

    def test_threshold_check_and_generation(self):
        small = self.invoice([{**self.line("1"), "godown": self.godown.id}])
        self.assertFalse(self.client.get(f"{API}/invoices/{small['id']}/eway-bill-check/").json()["required"])
        big = self.invoice([{**self.line("50"), "godown": self.godown.id}])
        self.assertTrue(self.client.get(f"{API}/invoices/{big['id']}/eway-bill-check/").json()["required"])
        ewb = self.post(f"invoices/{big['id']}/eway-bill/", {"distanceKm": 450, "vehicleNo": "KA01AB1234"})
        self.assertEqual(ewb["documentNumber"], big["invoiceNumber"])
        self.post(f"invoices/{big['id']}/eway-bill/", {"distanceKm": 10, "vehicleNo": "KA01AB1234"}, expect=400)
        moved = self.post(f"eway-bills/{ewb['id']}/update-vehicle/", {"vehicleNo": "KA02CD5678",
                                                                    "reason": "Breakdown"}, expect=200)
        self.assertEqual(len(moved["vehicleHistory"]), 2)

    def test_state_specific_intra_state_limit(self):
        settings = get_company_settings(self.tenant)
        settings.eway_bill_intra_state_thresholds = {"Karnataka": 100000}
        settings.save()
        inv = self.invoice([{**self.line("60"), "godown": self.godown.id}])  # 70,800 intra-state
        self.assertFalse(self.client.get(f"{API}/invoices/{inv['id']}/eway-bill-check/").json()["required"])

    def test_inter_state_job_work_always_needs_one(self):
        challan = self.post("delivery-challans/", {
            "customer": self.outstation.id, "dcDate": str(TODAY), "dcType": "NonBillable", "purpose": "JobWork",
            "items": [{"item": self.item.id, "quantity": "1", "rate": "100", "godown": self.godown.id}]})
        check = self.client.get(f"{API}/delivery-challans/{challan['id']}/eway-bill-check/").json()
        self.assertTrue(check["required"])

    def test_validity_and_cancel_window(self):
        generated = timezone.now()
        self.assertEqual(rules.eway_bill_valid_till(generated_at=generated, distance_km=450,
                                                    over_dimensional=False).date(),
                         generated.date() + datetime.timedelta(days=3))
        inv = self.invoice([{**self.line("50"), "godown": self.godown.id}])
        ewb = self.post(f"invoices/{inv['id']}/eway-bill/", {"distanceKm": 100, "vehicleNo": "KA01AB1234"})
        later = timezone.now() + datetime.timedelta(hours=25)
        with mock.patch("sales.compliance.actions.timezone.now", return_value=later):
            self.post(f"eway-bills/{ewb['id']}/cancel/", {"remark": "x"}, expect=400)
        self.post(f"eway-bills/{ewb['id']}/cancel/", {"remark": "x"}, expect=200)
        self.assertEqual(EWayBill.objects.get().status, EWayBill.STATUS_CANCELLED)


class ReturnAndCreditNoteTests(SalesTestBase):
    def billed(self, qty="10"):
        return self.invoice([{**self.line(qty), "godown": self.godown.id}])

    def test_sales_return_restocks_and_tracks_refunds(self):
        inv = self.billed()
        line = inv["items"][0]
        ret = self.post("returns/", {"customer": self.customer.id, "returnDate": str(TODAY),
                                     "salesInvoice": inv["id"], "amountRefunded": "1000",
                                     "items": [{"invoiceItem": line["id"], "item": self.item.id, "quantity": "2",
                                                "rate": "1000"}]})
        self.assertEqual((ret["status"], ret["balanceAmount"]), ("Partially Refunded", "1360.00"))
        self.assertEqual(self.stock(), Decimal("92"))
        self.post("returns/", {"customer": self.customer.id, "returnDate": str(TODAY), "salesInvoice": inv["id"],
                               "items": [{"invoiceItem": line["id"], "item": self.item.id, "quantity": "9",
                                          "rate": "1000"}]}, expect=400)
        self.post(f"invoices/{inv['id']}/cancel/", {}, expect=400)  # return exists

    def test_credit_note_rules(self):
        inv = self.billed()
        base = {"customer": self.customer.id, "creditNoteDate": str(TODAY), "salesInvoice": inv["id"],
                "items": [{"item": self.item.id, "quantity": "1", "rate": "100"}]}
        self.post("credit-notes/", {**base, "reason": "SalesReturn"}, expect=400)  # needs the return
        note = self.post("credit-notes/", {**base, "reason": "PostSaleDiscount"})
        self.assertEqual(note["netAmount"], "118.00")
        deadline = rules.credit_note_deadline(invoice_date=TODAY, fy_start_month=4)
        self.assertEqual(note["gstDeadline"], str(deadline))
        self.post("credit-notes/", {**base, "creditNoteDate": str(deadline + datetime.timedelta(days=1))},
                  expect=400)

    def test_credit_note_is_e_invoiced_when_invoice_is(self):
        settings = get_company_settings(self.tenant)
        settings.einvoice_enabled = True
        settings.save()
        inv = self.billed()
        note = self.post("credit-notes/", {"customer": self.customer.id, "creditNoteDate": str(TODAY),
                                           "salesInvoice": inv["id"], "reason": "PriceCorrection",
                                           "items": [{"item": self.item.id, "quantity": "1", "rate": "100"}]})
        done = self.post(f"credit-notes/{note['id']}/einvoice/generate/", {}, expect=200)
        self.assertEqual(done["einvoiceStatus"], "Generated")


    def test_list_filters_used_by_the_screens(self):
        settings = get_company_settings(self.tenant)
        settings.einvoice_enabled = True
        settings.save()
        first, second = self.billed(), self.billed()
        for inv in (first, second):
            self.post("credit-notes/", {"customer": self.customer.id, "creditNoteDate": str(TODAY),
                                        "salesInvoice": inv["id"], "reason": "PriceCorrection",
                                        "items": [{"item": self.item.id, "quantity": "1", "rate": "100"}]})
            self.post("returns/", {"customer": self.customer.id, "returnDate": str(TODAY), "salesInvoice": inv["id"],
                                   "items": [{"invoiceItem": inv["items"][0]["id"], "item": self.item.id,
                                              "quantity": "1", "rate": "1000"}]})
        notes = self.client.get(f"{API}/credit-notes/", {"sales_invoice": first["id"]}).json()
        self.assertEqual([n["invoiceNumber"] for n in notes], [first["invoiceNumber"]])
        returns = self.client.get(f"{API}/returns/", {"sales_invoice": second["id"]}).json()
        self.assertEqual([r["invoiceNumber"] for r in returns], [second["invoiceNumber"]])
        self.post(f"credit-notes/{notes[0]['id']}/einvoice/generate/", {}, expect=200)
        pending = self.client.get(f"{API}/credit-notes/", {"einvoice_status": "Pending"}).json()
        self.assertEqual([n["invoiceNumber"] for n in pending], [second["invoiceNumber"]])

        big = self.billed("50")
        self.post(f"invoices/{big['id']}/eway-bill/", {"distanceKm": 100, "vehicleNo": "KA01AB1234"})
        self.assertEqual(len(self.client.get(f"{API}/eway-bills/", {"document_type": "SalesInvoice"}).json()), 1)
        self.assertEqual(len(self.client.get(f"{API}/eway-bills/", {"document_type": "DeliveryChallan"}).json()), 0)
        tomorrow = str(TODAY + datetime.timedelta(days=1))
        self.assertEqual(len(self.client.get(f"{API}/eway-bills/", {"from": tomorrow}).json()), 0)

class PaymentAndMiscTests(SalesTestBase):
    def test_advance_on_order_is_set_off_once_on_its_invoice(self):
        so = self.sale_order("10", advanceReceived="5000")
        inv = self.post(f"sale-orders/{so['id']}/convert-to-invoice/", {"godown": self.godown.id})
        self.assertEqual((inv["advanceAdjusted"], inv["balanceAmount"]), ("5000.00", "6800.00"))
        self.assertEqual(SaleOrder.objects.get(pk=so["id"]).advance_balance, Decimal("0"))

    def test_received_cannot_exceed_total(self):
        self.invoice([{**self.line("1"), "godown": self.godown.id}], amountReceived="5000", expect=400)

    def test_free_item_is_zero_value_but_leaves_stock(self):
        inv = self.invoice([{**self.line("1"), "godown": self.godown.id},
                            {**self.line("1"), "godown": self.godown.id, "isFree": True}])
        self.assertEqual(inv["totalTaxable"], "1000.00")
        self.assertEqual(self.stock(), Decimal("98"))

    def test_invoice_numbering_uses_type_and_nature(self):
        inv = self.invoice([{**self.line("1"), "godown": self.godown.id}], taxNature="Labour")
        self.assertTrue(inv["invoiceNumber"].startswith("INV/"))
        self.assertEqual(SalesInvoice.objects.get(pk=inv["id"]).tax_nature, "Labour")

    def test_inactive_customer_blocked(self):
        self.customer.status = "Inactive"
        self.customer.save()
        self.post("sale-orders/", {"customer": self.customer.id, "soDate": str(TODAY), "items": [self.line()]},
                  expect=400)
