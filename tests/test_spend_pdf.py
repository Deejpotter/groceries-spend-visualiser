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


def _upload_pdf(client, lines, filename="invoice.pdf"):
    import io
    return client.post(
        "/spend/import",
        data={"file": (io.BytesIO(_minimal_pdf(lines)), filename), "store": "Woolworths"},
        content_type="multipart/form-data",
    )


REVIEW_LINES = [
    "Sub Total:Sub Total:",
    "Reusable bags: Reusable bags:",
    "Delivery Fee:Delivery Fee:",
    "Invoice Total:Invoice Total:",
    "Invoice total includes GST of:Invoice total includes GST of:",
    "Paid Amount:Paid Amount:",
    "1 Golden crumpets round 6 pack 1 1 $2.20 $2.20",
    "2 Tip top toast 600g 1 0 $4.40 $0.00",
    "$2.20",
    "$0.00",
    "$0.00",
    "$2.20",
    "$0.20",
    "$2.20",
    "Invoice/Order Number: 999001999Invoice/Order Number: 999001999",
    "Date: 07 Jul 2023Date: 07 Jul 2023",
]


def test_staged_edits_validate_and_autofill_total(db):
    from services.spend_import import (
        create_import_batch, get_import_batch, save_staged_edits,
    )
    meta = {"invoice_number": "777", "order_date": "2023-07-07", "subtotal": 4.40,
            "skipped": []}
    rows = [{"order_date": "2023-07-07", "basket_id": "777", "store": "Woolworths",
             "channel": None, "product_name": "Toast", "quantity": 2,
             "unit_price": 2.20, "line_total": 4.40, "cup_price": None, "stockcode": None}]
    token = create_import_batch(db, meta, rows)
    _, lines = get_import_batch(db, token)
    assert lines[0]["already"] is False
    # Blank total is filled from quantity x unit price.
    assert save_staged_edits(db, token, [{
        "id": lines[0]["id"], "product_name": "Toast",
        "quantity": "2", "unit_price": "2.20", "line_total": "", "excluded": False,
    }]) == []
    _, lines = get_import_batch(db, token)
    assert lines[0]["line_total"] == 4.40
    # Included lines need a name, quantity and total.
    assert save_staged_edits(db, token, [{
        "id": lines[0]["id"], "product_name": "",
        "quantity": "0", "unit_price": "", "line_total": "", "excluded": False,
    }]) != []
    # ...unless excluded.
    assert save_staged_edits(db, token, [{
        "id": lines[0]["id"], "product_name": "",
        "quantity": "0", "unit_price": "", "line_total": "", "excluded": True,
    }]) == []


def test_commit_inherits_ingredient_links(db):
    from services.spend_import import commit_import_batch, create_import_batch
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Toast', 'bakery', 'each')")
    ing = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity,"
               " line_total, ingredient_id) VALUES ('2023-01-01', 'old', 'Woolworths', 'Toast', 1, 2.0, ?)", (ing,))
    db.commit()
    meta = {"invoice_number": "778", "order_date": "2023-07-07", "subtotal": 2.0, "skipped": []}
    rows = [{"order_date": "2023-07-07", "basket_id": "778", "store": "Woolworths",
             "channel": None, "product_name": "Toast", "quantity": 1,
             "unit_price": 2.0, "line_total": 2.0, "cup_price": None, "stockcode": None}]
    token = create_import_batch(db, meta, rows)
    assert commit_import_batch(db, token) == (1, 0)
    assert db.execute("SELECT ingredient_id FROM purchases WHERE basket_id = '778'").fetchone()[0] == ing
    # Committed batches disappear from review; discarding works for pending ones.
    from services.spend_import import discard_import_batch, get_import_batch
    assert get_import_batch(db, token) == (None, [])
    token2 = create_import_batch(db, meta, rows)
    assert discard_import_batch(db, token2) is True
    assert get_import_batch(db, token2) == (None, [])


def test_invoice_upload_and_reimport_message(client, db):
    login(client)
    first = _upload_pdf(client, REVIEW_LINES)
    assert first.status_code == 302 and "/spend/import/review/" in first.headers["Location"]
    token = first.headers["Location"].rsplit("/", 1)[-1]
    # Nothing reaches purchases before confirmation.
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0
    review = client.get(f"/spend/import/review/{token}")
    assert review.status_code == 200
    assert b"Golden crumpets" in review.data and b"Tip top toast" in review.data
    assert b"$2.20" in review.data  # read total vs subtotal shown
    line = db.execute("SELECT id FROM import_staged_lines").fetchone()
    done = client.post(f"/spend/import/review/{token}", data={
        "action": "confirm",
        "line_id": [str(line["id"])],
        "product_name": ["Golden crumpets round 6 pack"],
        "quantity": ["1"], "unit_price": ["2.20"], "line_total": ["2.20"],
    }, follow_redirects=True)
    assert b"999001999" in done.data
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
    # Re-uploading the same invoice flags every line as already imported.
    again = client.get(_upload_pdf(client, REVIEW_LINES).headers["Location"])
    assert b"already imported" in again.data


def test_invoice_net_suffixed_weight_line():
    """'0.19 kg NET' (variable-weight veg) must parse alone, not fuse into the next row."""
    text = """Sub Total:Sub Total:
Reusable bags: Reusable bags:
Delivery Fee:Delivery Fee:
Invoice Total:Invoice Total:
Invoice total includes GST of:Invoice total includes GST of:
Paid Amount:Paid Amount:
40 fresh zucchini green pdm each 1 1 $1.58 $1.58
41 mushrooms cups loose pdm per 200g 0.20 0.19 kg NET $12.90 $2.49
42 Wow spring onion bunch each 1 1 $3.30 $3.30
$7.37
$0.00
$0.00
$7.37
$0.67
$7.37
Invoice/Order Number: 328324774Invoice/Order Number: 328324774
Date: 28 Sep 2026Date: 28 Sep 2026
"""
    meta, rows, warnings = parse_woolworths_invoice_text(text)
    assert meta["subtotal"] == 7.37
    assert [r["product_name"] for r in rows] == [
        "fresh zucchini green pdm each",
        "mushrooms cups loose pdm per 200g",
        "Wow spring onion bunch each",
    ]
    assert rows[1]["quantity"] == 0.19 and rows[1]["line_total"] == 2.49
    assert not any("subtotal is" in w for w in warnings)


def test_invoice_paper_bags_and_split_fees_map_totals():
    """Paper bags + separate collection/service fees still map all seven totals."""
    text = """Sub Total:Sub Total:
Thank you for working with us towards a greener future. Paper bags: Paper bags:
Collection fee
Service fees
Invoice Total:Invoice Total:
Invoice total includes GST of:Invoice total includes GST of:
Paid Amount:Paid Amount:
1 Golden crumpets round 6 pack 1 1 $2.20 $2.20
$313.88
$2.00
$0.00
$0.00
$315.88
$9.61
$315.88
Invoice/Order Number: 328324774Invoice/Order Number: 328324774
Date: 28 Sep 2026Date: 28 Sep 2026
"""
    meta, rows, warnings = parse_woolworths_invoice_text(text)
    assert meta["subtotal"] == 313.88
    assert meta["bags"] == 2.00
    assert meta["total"] == 315.88
    assert meta["gst"] == 9.61
    assert meta["paid"] == 315.88
