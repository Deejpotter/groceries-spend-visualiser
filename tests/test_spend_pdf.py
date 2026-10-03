"""Woolworths PDF tax-invoice import: parsing, totals check, dedupe, routes."""

import io

import pytest

from conftest import login
from services.spend_import import (
    import_purchases,
    parse_woolworths_invoice_pdf,
    parse_woolworths_invoice_text,
)

# Shaped like real pypdf extraction (doubled headers/categories, NBSPs),
# with synthetic products. Subtotal: 5.20 + 2.20 + 4.50 + 7.60 + 5.50 = 25.00.
INVOICE_TEXT = """Sub Total:Sub Total:
Thank you for working with us towards a greener future.  Reusable bags: Reusable bags:
Delivery Fee:Delivery Fee:
Invoice Total:Invoice Total:
Invoice total includes GST of:Invoice total includes GST of:
Paid Amount:Paid Amount:
SuppliedSupplied
LineLine DescriptionDescription OrderedOrdered SuppliedSupplied PricePrice AmountAmount
BabyBaby
1 * Test baby bath
500ml 1 1 $8.80 $8.80
BakeryBakery
2 Golden crumpets round 6 pack 1 1 $2.20 $2.20
2 \u00a0\u00a0\u00a0(Sub) Test raisin toast 520G 0 1 $4.50 $4.50
3 Missing loaf 600g 1 0 $4.40 $0.00
Out Of StockOut Of Stock
4 Free sticker each 1 1 $0.00 $0.00
Serviced DeliServiced Deli
5 Test bacon per kg 0.40 0.40 $19.00 $7.60
MeatMeat
6 Test chicken fillet 0.50 0.50 kg $11.00 $5.50
$25.00
$0.00
$0.00
$25.00
$2.01
$25.00
Tax Invoice Page 1 of 1
Invoice/Order Number: 999001999Invoice/Order Number: 999001999
Date: 07 Jul 2023Date: 07 Jul 2023
"""

# Fix the baby-bath line so the fixture totals agree (8.80 would break it).
INVOICE_TEXT = INVOICE_TEXT.replace(
    "1 * Test baby bath\n500ml 1 1 $8.80 $8.80",
    "9 Test eggs 700g 1 1 $5.20 $5.20",
)


def _minimal_pdf(lines):
    """One-page PDF with the given text lines (for the bytes-level path)."""
    text = "BT /F1 12 Tf 72 750 Td 14 TL " + "".join(
        "(%s) Tj T* " % line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ) + "ET"
    body = text.encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = [b"%PDF-1.4"]
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(sum(len(p) + 1 for p in out))
        out.append(b"%d 0 obj\n" % i + obj + b"\nendobj")
    xref_at = sum(len(p) + 1 for p in out)
    out.append(b"xref\n0 %d\n0000000000 65535 f " % (len(objs) + 1))
    out.append(b"\n".join(b"%010d 00000 n " % o for o in offsets))
    out.append(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_at))
    return b"\n".join(out)


def test_invoice_text_reads_items_subs_and_weights():
    meta, rows, warnings = parse_woolworths_invoice_text(INVOICE_TEXT)
    assert meta["invoice_number"] == "999001999"
    assert meta["order_date"] == "2023-07-07"
    assert meta["subtotal"] == 25.00
    by_name = {r["product_name"]: r for r in rows}
    assert by_name["Golden crumpets round 6 pack"]["quantity"] == 1
    assert by_name["Golden crumpets round 6 pack"]["unit_price"] == 2.20
    assert by_name["Test raisin toast 520G"]["quantity"] == 1  # (Sub), ordered 0
    assert by_name["Test bacon per kg"]["quantity"] == 0.40  # deli weight
    assert by_name["Test bacon per kg"]["line_total"] == 7.60
    assert by_name["Test chicken fillet"]["line_total"] == 5.50  #explicit kg unit
    assert "Missing loaf 600g" not in by_name  # supplied 0
    assert "Free sticker each" not in by_name  # $0.00
    assert not any("BakeryBakery" in name for name in by_name)
    assert not any("subtotal is" in w for w in warnings)  # lines total exactly the subtotal


def test_invoice_text_rejects_totals_mismatch():
    bad = INVOICE_TEXT.replace("$25.00\n$0.00\n$0.00\n$25.00", "$99.99\n$0.00\n$0.00\n$99.99")
    meta, rows, warnings = parse_woolworths_invoice_text(bad)
    assert len(rows) == 5
    assert any("subtotal is" in w for w in warnings)


def test_invoice_text_rejects_non_invoices():
    meta, rows, warnings = parse_woolworths_invoice_text("just some text\nno numbers here")
    assert meta == {} and rows == []
    assert warnings


def test_invoice_pdf_bytes_rejected_when_unreadable():
    meta, rows, warnings = parse_woolworths_invoice_pdf(b"not a pdf at all")
    assert meta == {} and rows == []
    assert warnings


def test_invoice_pdf_round_trip_through_pypdf():
    lines = [
        "Sub Total:Sub Total:",
        "Reusable bags: Reusable bags:",
        "Delivery Fee:Delivery Fee:",
        "Invoice Total:Invoice Total:",
        "Invoice total includes GST of:Invoice total includes GST of:",
        "Paid Amount:Paid Amount:",
        "1 Golden crumpets round 6 pack 1 1 $2.20 $2.20",
        "$2.20",
        "$0.00",
        "$0.00",
        "$2.20",
        "$0.20",
        "$2.20",
        "Invoice/Order Number: 999001999Invoice/Order Number: 999001999",
        "Date: 07 Jul 2023Date: 07 Jul 2023",
    ]
    meta, rows, warnings = parse_woolworths_invoice_pdf(_minimal_pdf(lines))
    assert meta["invoice_number"] == "999001999"
    assert [r["product_name"] for r in rows] == ["Golden crumpets round 6 pack"]
    assert warnings == []


def test_invoice_import_dedupes_by_order_number(db):
    meta, rows, _ = parse_woolworths_invoice_text(INVOICE_TEXT)
    assert import_purchases(db, rows) == (5, 0)
    assert import_purchases(db, rows) == (0, 5)
    assert db.execute(
        "SELECT COUNT(*) FROM purchases WHERE basket_id = ?", (meta["invoice_number"],)
    ).fetchone()[0] == 5


def test_invoice_upload_and_reimport_message(client, db):
    login(client)
    lines = [
        "Sub Total:Sub Total:",
        "Reusable bags: Reusable bags:",
        "Delivery Fee:Delivery Fee:",
        "Invoice Total:Invoice Total:",
        "Invoice total includes GST of:Invoice total includes GST of:",
        "Paid Amount:Paid Amount:",
        "1 Golden crumpets round 6 pack 1 1 $2.20 $2.20",
        "$2.20",
        "$0.00",
        "$0.00",
        "$2.20",
        "$0.20",
        "$2.20",
        "Invoice/Order Number: 999001999Invoice/Order Number: 999001999",
        "Date: 07 Jul 2023Date: 07 Jul 2023",
    ]
    pdf = _minimal_pdf(lines)
    first = client.post(
        "/spend/import",
        data={"file": (io.BytesIO(pdf), "invoice.pdf"), "store": "Woolworths"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"999001999" in first.data
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
    second = client.post(
        "/spend/import",
        data={"file": (io.BytesIO(pdf), "invoice.pdf"), "store": "Woolworths"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"already imported" in second.data
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
