"""One product, two spellings: the order CSV and the invoices ("... pdm ...") group and link together."""

from datetime import date

from services.mutations import link_product, name_variants
from services.purchase_patterns import build_patterns, make_purchase, normalise_key, product_name_key
from services.spend_import import import_purchases, link_name_variants
from services.usage import counts_by_key, make_count

CSV_NAME = "Woolworths Peach In Strawberry Jelly 4 pack"
INVOICE_NAME = "Woolworths peach in strawberry jelly pdm 4 pack"


def test_name_key_drops_invoice_marker_case_and_spacing():
    assert product_name_key(INVOICE_NAME) == product_name_key(CSV_NAME) == "woolworths peach in strawberry jelly 4 pack"
    assert product_name_key("PDM  Bananas") == "bananas"
    assert product_name_key("Pdmx cheese") == "pdmx cheese"  # only the whole word
    assert normalise_key("ing:12") == "ing:12"


def test_both_spellings_are_one_pattern():
    rows = [make_purchase("2026-09-01", CSV_NAME), make_purchase("2026-09-15", INVOICE_NAME),
            make_purchase("2026-10-01", CSV_NAME)]
    patterns = build_patterns(rows, date(2026, 11, 1))
    assert [(p["key"], p["buys"]) for p in patterns] == [("prod:woolworths peach in strawberry jelly 4 pack", 3)]


def test_counts_saved_under_an_old_key_still_match():
    assert list(counts_by_key([make_count(f"prod:{INVOICE_NAME}")])) == [normalise_key(f"prod:{CSV_NAME}")]


def _buy(db, day, name, ingredient_id=None):
    db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity, unit_price, line_total,"
               " ingredient_id) VALUES (?, ?, 'Woolworths', ?, 1, 4.5, 4.5, ?)", (day, day + name, name, ingredient_id))


def _links(db):
    return dict(db.execute("SELECT product_name, MAX(ingredient_id) FROM purchases GROUP BY product_name").fetchall())


def test_linking_one_spelling_links_the_other(db):
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (7, 'Jelly cups', 'snacks', 'each')")
    _buy(db, "2026-09-01", CSV_NAME)
    _buy(db, "2026-09-15", INVOICE_NAME)
    _buy(db, "2026-09-20", "Woolworths peach in mango jelly 4 pack")
    db.commit()
    assert sorted(name_variants(db, CSV_NAME)) == sorted([CSV_NAME, INVOICE_NAME])
    link_product(db, INVOICE_NAME, "7", update_price=False)
    assert _links(db) == {CSV_NAME: 7, INVOICE_NAME: 7, "Woolworths peach in mango jelly 4 pack": None}
    link_product(db, CSV_NAME, "")
    assert set(_links(db).values()) == {None}


def test_import_inherits_a_link_made_on_the_other_spelling(db):
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (7, 'Jelly cups', 'snacks', 'each')")
    _buy(db, "2026-09-01", CSV_NAME, 7)
    db.commit()
    import_purchases(db, [{"order_date": "2026-10-01", "basket_id": "inv-1", "store": "Woolworths", "channel": None,
                           "product_name": INVOICE_NAME, "quantity": 1, "unit_price": 4.5, "line_total": 4.5,
                           "cup_price": None, "stockcode": None}])
    assert _links(db)[INVOICE_NAME] == 7


def test_backfill_links_old_twins_but_not_conflicts(db):
    db.executemany("INSERT INTO ingredients (id, name, category, unit) VALUES (?, ?, 'snacks', 'each')",
                   [(7, "Jelly cups"), (8, "Bananas"), (9, "Fruit")])
    _buy(db, "2026-09-01", CSV_NAME, 7)
    _buy(db, "2026-09-15", INVOICE_NAME)
    _buy(db, "2026-09-16", INVOICE_NAME)
    _buy(db, "2026-09-01", "Cavendish Bananas each", 8)
    _buy(db, "2026-09-02", "cavendish bananas each", 9)  # two links already disagree: leave the third alone
    _buy(db, "2026-09-03", "cavendish bananas pdm each")
    db.commit()
    assert link_name_variants(db) == 2
    links = _links(db)
    assert links[INVOICE_NAME] == 7 and links["cavendish bananas pdm each"] is None
    assert link_name_variants(db) == 0
