"""Recipe alternatives: rows sharing an option group are bought as one, the cheapest."""

from conftest import login
from services.meal_patterns import recipe_cost_per_serving
from services.shopping_list_generator import (
    choose_options, generate_shopping_list, make_ingredient, make_plan_entry,
    make_recipe_dict, make_recipe_ingredient,
)

INGREDIENTS = {
    1: make_ingredient(1, "Chicken Thigh", unit="kg", category="meat", price=13.0),
    2: make_ingredient(2, "Tofu", unit="kg", category="meat", price=8.0),
    3: make_ingredient(3, "Coconut Cream", unit="L", price=6.0),
    4: make_ingredient(4, "Mystery Protein", unit="kg"),
}


def _curry(*extra):
    return [
        make_recipe_ingredient(1, 0.6, option_group="protein"),
        make_recipe_ingredient(2, 0.45, option_group="Protein "),
        make_recipe_ingredient(3, 0.4),
        *extra,
    ]


class TestChooseOptions:
    def test_cheapest_option_wins(self):
        chosen = choose_options(_curry(), INGREDIENTS)
        assert [l["ingredient_id"] for l in chosen] == [2, 3]

    def test_pantry_stock_beats_price(self):
        chosen = choose_options(_curry(), INGREDIENTS, {1: (1.0, "kg")})
        assert [l["ingredient_id"] for l in chosen] == [1, 3]

    def test_not_enough_in_pantry_falls_back_to_price(self):
        chosen = choose_options(_curry(), INGREDIENTS, {1: (0.2, "kg")})
        assert [l["ingredient_id"] for l in chosen] == [2, 3]

    def test_unpriced_options_lose_to_priced_and_keep_order(self):
        links = [make_recipe_ingredient(4, 1, option_group="p"), make_recipe_ingredient(1, 1, option_group="p")]
        assert [l["ingredient_id"] for l in choose_options(links, INGREDIENTS)] == [1]
        unpriced = [make_recipe_ingredient(4, 1, option_group="p"), make_recipe_ingredient(9, 1, option_group="p")]
        assert [l["ingredient_id"] for l in choose_options(unpriced, INGREDIENTS)] == [4]

    def test_no_groups_keeps_everything(self):
        links = [make_recipe_ingredient(1, 1), make_recipe_ingredient(2, 1)]
        assert choose_options(links, INGREDIENTS) == links


def test_shopping_list_buys_one_option():
    grouped = generate_shopping_list(
        [make_plan_entry(recipe_id=1)], {1: make_recipe_dict(1, "Curry")},
        {1: _curry()}, INGREDIENTS)
    names = [item["ingredient_name"] for items in grouped.values() for item in items]
    assert sorted(names) == ["Coconut Cream", "Tofu"]


def test_cost_per_serving_counts_one_option():
    cost = recipe_cost_per_serving({"servings": 4}, _curry(), INGREDIENTS)
    assert cost == round((0.45 * 8.0 + 0.4 * 6.0) / 4, 2)


def test_recipe_form_saves_and_shows_option_groups(client, db):
    login(client)
    for name in ("Chicken Thigh", "Tofu"):
        client.post("/ingredients/add", data={"name": name, "category": "meat", "unit": "kg"})
    ids = [str(r["id"]) for r in db.execute("SELECT id FROM ingredients ORDER BY id")]
    resp = client.post("/recipes/add", data={
        "name": "Curry", "servings": "8", "covers_days": "2",
        "ingredient_ids": ids, "quantities": ["0.6", "0.45"],
        "unit_overrides": ["", ""], "option_groups": ["protein", "protein"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    groups = [r["option_group"] for r in db.execute("SELECT option_group FROM recipe_ingredients")]
    assert groups == ["protein", "protein"]
    assert b"or \xc2\xb7 protein" in resp.data
    recipe_id = db.execute("SELECT id FROM recipes").fetchone()["id"]
    edit = client.get(f"/recipes/edit/{recipe_id}")
    assert b'value="protein"' in edit.data
