"""PDF export (reportlab, pure Python, no system libraries needed).

Same inputs as exports.build_workbook: registry Col objects plus row tuples lined up
with them. Landscape page, wrapped cells, header row repeated on every page, shaded
parent columns, "Page x of y" footer.

Non-Latin text (Tamil, Hindi...) and the rupee sign need a Unicode TTF font: set
REPORTS_PDF_FONT (and optionally REPORTS_PDF_FONT_BOLD) in settings to a .ttf path.
Without it the built-in Helvetica is used, which only covers Latin text."""
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

HEADER_BG = colors.HexColor("#1F4E78")
PARENT_HEADER_BG = colors.HexColor("#2E75B6")
STRIPE_BG = colors.HexColor("#F5F8FC")
GRID = colors.HexColor("#BFBFBF")

_fonts = None


def _font_names():
    global _fonts
    if _fonts is None:
        regular, bold = "Helvetica", "Helvetica-Bold"
        path = getattr(settings, "REPORTS_PDF_FONT", None)
        if path:
            pdfmetrics.registerFont(TTFont("ReportFont", path))
            regular = bold = "ReportFont"
            bold_path = getattr(settings, "REPORTS_PDF_FONT_BOLD", None)
            if bold_path:
                pdfmetrics.registerFont(TTFont("ReportFont-Bold", bold_path))
                bold = "ReportFont-Bold"
        _fonts = (regular, bold)
    return _fonts


def _text(col, value):
    if value is None:
        return ""
    if col.type == "bool":
        return "Yes" if value else "No"
    if col.type == "datetime":
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return f"{value:%d-%b-%Y %H:%M}"
    if col.type == "date":
        return f"{value:%d-%b-%Y}"
    if col.type == "number" and isinstance(value, (int, float, Decimal)):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _widths(heads, body, available):
    """Column widths proportional to the longest heading/value (sampled), then scaled to fit."""
    weights = []
    for i, head in enumerate(heads):
        longest = max([len(head)] + [len(r[i]) for r in body[:300]])
        weights.append(min(max(longest, 5), 32) + 2)
    scale = available / sum(weights)
    return [w * scale for w in weights]


def build_pdf(*, title, columns, rows, business=None, include_serial=True, parent_once=False, generated_at=None):
    generated_at = generated_at or timezone.localtime()
    regular, bold = _font_names()

    raw = list(rows)
    body = [[_text(c, v) for c, v in zip(columns, row)] for row in raw]
    offset = 1 if include_serial else 0
    ncols = len(columns) + offset

    page = landscape(A3 if ncols > 14 else A4)
    margin = 10 * mm
    font_size = 8 if ncols <= 8 else 7 if ncols <= 12 else 6
    leading = font_size + 2

    cell = ParagraphStyle("cell", fontName=regular, fontSize=font_size, leading=leading, alignment=TA_LEFT)
    cell_right = ParagraphStyle("cell_r", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("head", fontName=bold, fontSize=font_size, leading=leading,
                          alignment=TA_CENTER, textColor=colors.white)
    centre = ParagraphStyle("centre", fontName=regular, fontSize=9, leading=12, alignment=TA_CENTER)

    heads = (["S.No"] if include_serial else []) + [c.label for c in columns]
    sized_body = ([[str(n)] + r for n, r in enumerate(body, start=1)] if include_serial else body)
    widths = _widths(heads, sized_body, page[0] - 2 * margin)

    def para(text, col):
        style = cell_right if (col is None or col.type == "number") else cell
        return Paragraph(escape(text), style)

    table_rows = [[Paragraph(escape(h), head) for h in heads]]
    for n, r in enumerate(body, start=1):
        cells = [para(v, c) for v, c in zip(r, columns)]
        table_rows.append(([para(str(n), None)] if include_serial else []) + cells)

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE_BG]),
    ]
    parent_idx = [i for i, c in enumerate(columns) if c.group]
    for i in parent_idx:
        style.append(("BACKGROUND", (i + offset, 0), (i + offset, 0), PARENT_HEADER_BG))
    if parent_once and parent_idx:
        # with "once per parent" a row that still carries parent values starts a new parent
        for r_i, row in enumerate(raw, start=1):
            if any(row[i] is not None for i in parent_idx):
                style.append(("LINEABOVE", (0, r_i), (-1, r_i), 1.2, HEADER_BG))

    table = LongTable(table_rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(style))

    story = []
    if business:
        story.append(Paragraph(escape(business["name"]),
                               ParagraphStyle("biz", parent=centre, fontName=bold, fontSize=14, leading=17)))
        for line in (business.get("address"), business.get("contact")):
            if line:
                story.append(Paragraph(escape(line), ParagraphStyle("biz2", parent=centre, fontSize=8, leading=10)))
        story.append(Spacer(1, 3))
        story.append(HRFlowable(width="100%", thickness=1.2, color=HEADER_BG))
        story.append(Spacer(1, 4))
    story.append(Paragraph(escape(f"{title} Report"), ParagraphStyle("t", parent=centre, fontName=bold, fontSize=11)))
    story.append(Paragraph(f"Generated on: {generated_at:%d-%b-%Y %H:%M}",
                           ParagraphStyle("g", parent=centre, fontSize=8, textColor=colors.grey)))
    story.append(Spacer(1, 6))
    story.append(table)
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Total records: {len(raw)}" if raw else "No records found.",
                           ParagraphStyle("tot", parent=cell, fontName=bold, fontSize=9, leading=12)))

    class NumberedCanvas(canvas.Canvas):
        """Second pass so each page can say 'Page x of y'."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont(regular, 8)
                self.setFillColor(colors.grey)
                self.drawString(margin, 6 * mm, f"{title} Report")
                self.drawRightString(page[0] - margin, 6 * mm, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=14 * mm, title=f"{title} Report")
    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()
