# docnumbering/tests.py
#
# The assembly rule is "built from the values entered": separators only
# where selected and only between two parts that both have values.

import datetime

from django.test import TestCase
from rest_framework.test import APITestCase

from docnumbering.models import DocumentNumberFormat
from docnumbering.services import MissingNumberFormat, next_number, preview
from tenants.services import get_company_settings, get_current_tenant


def make_format(tenant, **fields):
    defaults = {"doc": "PO", "from_date": datetime.date(2024, 5, 21), "format_digit": 1}
    return DocumentNumberFormat.objects.create(tenant=tenant, **{**defaults, **fields})


class AssemblyRuleTests(TestCase):
    def setUp(self):
        self.tenant = get_current_tenant(None)

    def fmt(self, **fields):
        return preview(DocumentNumberFormat(tenant=self.tenant, **{
            "doc": "X", "from_date": datetime.date(2024, 5, 21), "format_digit": 1, **fields}))

    def test_confirmed_examples(self):
        self.assertEqual(self.fmt(prefix="P", prefix_separator="/", doc_separator="/",
                                  fin_year_1=2, fin_year_2=2, year_separator="-"), "P/1/24-25")
        self.assertEqual(self.fmt(prefix="P", format_digit=5, is_year_first=True,
                                  fin_year_1=2, fin_year_2=2), "P242500001")

    def test_legacy_samples(self):
        self.assertEqual(self.fmt(prefix="DN", prefix_separator="/", doc_separator="/",
                                  fin_year_1=2, fin_year_2=2, year_separator="-"), "DN/1/24-25")
        self.assertEqual(self.fmt(prefix="PA", format_digit=4, is_year_first=True,
                                  fin_year_1=2, fin_year_2=2), "PA24250001")

    def test_no_dangling_separators(self):
        # Separators selected, but nothing on one side of them.
        self.assertEqual(self.fmt(prefix_separator="/", doc_separator="/", format_digit=3), "001")
        self.assertEqual(self.fmt(prefix="P", prefix_separator="-", doc_separator="/"), "P-1")

    def test_mid_text_and_suffix(self):
        self.assertEqual(self.fmt(prefix="P", prefix_separator="/", doc_separator="/", mid_text="A",
                                  mid_text_separator="-", fin_year_1=4, fin_year_2=2, year_separator="-",
                                  suffix="X", suffix_separator="/"), "P/1/A-2024-25/X")

    def test_year_follows_the_financial_year_not_the_calendar_year(self):
        # 10 Feb 2025 is still FY 24-25 with an April start.
        self.assertEqual(self.fmt(from_date=datetime.date(2025, 2, 10), fin_year_1=2, fin_year_2=2,
                                  year_separator="-", doc_separator="/"), "1/24-25")
        settings = get_company_settings(self.tenant)
        settings.financial_year_start_month = 1
        settings.save()
        self.assertEqual(self.fmt(from_date=datetime.date(2025, 2, 10), fin_year_1=2, fin_year_2=2,
                                  year_separator="-", doc_separator="/"), "1/25-26")


class NextNumberTests(TestCase):
    def setUp(self):
        self.tenant = get_current_tenant(None)

    def test_issues_sequentially_from_the_entered_doc_no(self):
        make_format(self.tenant, prefix="PO", prefix_separator="/", doc_no=57)
        self.assertEqual(next_number(self.tenant, "PO"), "PO/57")
        self.assertEqual(next_number(self.tenant, "PO"), "PO/58")
        self.assertEqual(DocumentNumberFormat.objects.get().issued_count, 2)

    def test_most_specific_series_wins_then_falls_back(self):
        make_format(self.tenant, doc="PR", prefix="PR")
        make_format(self.tenant, doc="PR", doc_type="General", prefix="GPR")
        self.assertEqual(next_number(self.tenant, "PR", doc_type="General"), "GPR1")
        self.assertEqual(next_number(self.tenant, "PR", doc_type="Item"), "PR1")

    def test_missing_format_is_a_clear_error(self):
        # PI is configured, but its only format is switched off.
        make_format(self.tenant, doc="PI", is_active=False)
        with self.assertRaises(MissingNumberFormat):
            next_number(self.tenant, "PI")

    def test_unconfigured_document_gets_its_default_format(self):
        number = next_number(self.tenant, "PI")
        self.assertTrue(number.startswith("PI/0001/"), number)


class FormatScreenApiTests(APITestCase):
    def setUp(self):
        self.tenant = get_current_tenant(None)

    def payload(self, **fields):
        return {"doc": "PO", "fromDate": "2024-05-21", "prefix": "P", "prefixSeparator": "/",
                "docSeparator": "/", "formatDigit": 1, "finYear1": 2, "finYear2": 2, "yearSeparator": "-",
                "docNo": 1, **fields}

    def test_create_shows_final_doc_format_and_accepts_starting_doc_no(self):
        response = self.client.post("/api/document-number-formats/", self.payload(docNo=57), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.json()["finalDocFormat"], "P/57/24-25")

    def test_second_active_format_for_same_document_is_refused(self):
        self.client.post("/api/document-number-formats/", self.payload(), format="json")
        response = self.client.post("/api/document-number-formats/", self.payload(), format="json")
        self.assertEqual(response.status_code, 400)

    def test_format_is_locked_once_it_has_issued_a_number(self):
        created = self.client.post("/api/document-number-formats/", self.payload(), format="json").json()
        next_number(self.tenant, "PO")
        url = f"/api/document-number-formats/{created['id']}/"
        self.assertEqual(self.client.patch(url, {"prefix": "Q"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)
