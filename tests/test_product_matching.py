"""Unit tests for product → ingredient matching (pure functions)."""

from services.product_matching import (
    suggest_ingredient,
    suggest_links,
    similarity,
    tokens,
)


class TestTokens:
    def test_strips_sizes_brand_and_punctuation(self):
        result = tokens("Woolworths Full Cream Milk 2L")
        assert "milk" in result
        assert "cream" in result
        assert "woolworths" not in result
        assert "2l" not in result

    def test_drops_brackets_but_keeps_qualifiers(self):
        assert tokens("Milk (Lactose Free)") == {"milk", "lactose", "free"}

    def test_empty_name_returns_empty_set(self):
        assert tokens(None) == set()
        assert tokens("") == set()


class TestSimilarity:
    def test_identical_names_score_one(self):
        assert similarity("Full Cream Milk", "Full Cream Milk") == 1.0

    def test_unrelated_names_score_zero(self):
        assert similarity("Dishwashing Liquid", "Chicken Breast") == 0.0

    def test_partial_overlap_scores_between(self):
        score = similarity("Woolworths Full Cream Milk 2L", "Milk")
        assert 0 < score < 1.0

    def test_size_and_brand_noise_does_not_change_score(self):
        assert similarity("Woolworths Full Cream Milk 2L", "Milk (Full Cream)") == 1.0


class TestSuggestIngredient:
    ingredients = [
        {"id": 1, "name": "Milk (Full Cream)", "display_name": "Milk"},
        {"id": 2, "name": "Chicken Breast Fillets", "display_name": "Chicken"},
        {"id": 3, "name": "Dishwashing Liquid", "display_name": None},
    ]

    def test_matches_obvious_product(self):
        match = suggest_ingredient("Woolworths Full Cream Milk 2L", self.ingredients)
        assert match["ingredient_id"] == 1
        assert match["score"] >= 0.5

    def test_returns_none_for_unrelated_product(self):
        assert suggest_ingredient("Cat Litter Odour Control", self.ingredients) is None

    def test_never_suggests_without_shared_words(self):
        assert suggest_ingredient("Cat Litter Odour Control", self.ingredients, threshold=0.0) is None


class TestSuggestLinks:
    def test_skips_already_linked_products(self):
        products = [
            {"product_name": "Full Cream Milk 2L", "ingredient_id": 1},
            {"product_name": "Chicken Breast 500g", "ingredient_id": None},
        ]
        result = suggest_links(products, TestSuggestIngredient.ingredients)
        assert "Full Cream Milk 2L" not in result
        assert result["Chicken Breast 500g"]["ingredient_id"] == 2

    def test_empty_inputs_are_safe(self):
        assert suggest_links([], []) == {}
        assert suggest_links(None, None) == {}


# ---------------------------------------------------------------------------
# Containment scoring, plurals, filler words, roles
# ---------------------------------------------------------------------------

from services.product_matching import (  # noqa: E402
    classify_product, confidence, match_score, product_role, singular, suggest_ingredient as _suggest,
    suggest_links as _links,
)

INGREDIENTS = [{"id": 1, "name": "Milk"}, {"id": 2, "name": "Cream"}, {"id": 3, "name": "Banana"},
               {"id": 4, "name": "Eggs"}, {"id": 5, "name": "Onion"}, {"id": 6, "name": "Chicken"},
               {"id": 7, "name": "Chicken breast"}, {"id": 8, "name": "Tomato"}]


class TestRealProductNames:
    def test_ingredient_contained_in_a_longer_product_name_matches(self):
        assert _suggest("Woolworths Full Cream Milk 2l", INGREDIENTS)["ingredient_id"] == 1

    def test_noun_last_wins_over_a_descriptive_word(self):
        # 'Full Cream Milk' is milk, not cream.
        assert match_score("Full Cream Milk 2L", "Milk") > match_score("Full Cream Milk 2L", "Cream")

    def test_plurals_fold_to_singular(self):
        assert _suggest("Woolworths Brown Onions 1kg", INGREDIENTS)["ingredient_id"] == 5
        assert _suggest("Cavendish Bananas Each", INGREDIENTS)["ingredient_id"] == 3
        assert _suggest("Truss Tomatoes Loose", INGREDIENTS)["ingredient_id"] == 8

    def test_filler_words_do_not_dilute_the_match(self):
        match = _suggest("Woolworths Free Range Eggs 12 Pack", INGREDIENTS)
        assert match["ingredient_id"] == 4 and match["confidence"] == "high"

    def test_more_specific_ingredient_wins(self):
        assert _suggest("Coles Chicken Breast Fillets 1kg", INGREDIENTS)["ingredient_id"] == 7

    def test_match_reports_the_words_it_matched_on(self):
        assert _suggest("Woolworths Brown Onions 1kg", INGREDIENTS)["matched_on"] == ["onion"]

    def test_unrelated_household_product_has_no_match(self):
        assert _suggest("Finish Dishwasher Tablets 50 Pack", INGREDIENTS) is None


class TestSingular:
    def test_rules(self):
        assert singular("berries") == "berry"
        assert singular("potatoes") == "potato"
        assert singular("peaches") == "peach"
        assert singular("eggs") == "egg"
        assert singular("hummus") == "hummus"
        assert singular("glass") == "glass"


class TestConfidence:
    def test_bands(self):
        assert confidence(0.9) == "high"
        assert confidence(0.7) == "medium"
        assert confidence(0.5) == "low"


class TestRoles:
    def test_food_by_default(self):
        assert classify_product("Woolworths Full Cream Milk 2L") == "food"

    def test_household(self):
        assert classify_product("Finish Dishwasher Tablets 50 Pack") == "household"
        assert classify_product("Quilton Toilet Tissue 18 Pack") == "household"

    def test_personal_care_pet_baby_treat_fee(self):
        assert classify_product("Colgate Toothpaste 175g") == "personal_care"
        assert classify_product("Pedigree Dog Food 1.2kg") == "pet"
        assert classify_product("Huggies Nappies Size 4") == "baby"
        assert classify_product("Smiths Chips Original 170g") == "treat"
        assert classify_product("Delivery Fee") == "fee"

    def test_cooking_oil_spray_is_food(self):
        assert classify_product("Bertolli Olive Oil Spray 400g") == "food"

    def test_user_decision_overrides_guess(self):
        decisions = {"Smiths Chips Original 170g": {"role": "food"}}
        assert product_role("Smiths Chips Original 170g", decisions) == "food"

    def test_non_food_products_get_no_link_suggestion(self):
        rows = [{"product_name": "Woolworths Milk 2L", "role": "household"}]
        assert _links(rows, INGREDIENTS) == {}
