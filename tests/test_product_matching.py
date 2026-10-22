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
