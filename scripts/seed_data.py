"""Seed the Grocery Visualiser database with ingredients and recipes.

Ingredients are sourced from the user's Woolworths order history
(data/woolworths_order_history.csv) — the most frequently purchased
food items, with prices from the latest purchase.

Recipes are based on the KitchenOwl recipe list and Woolworths saved
meal lists documented in krasus/docs/others/woolworths-meals-and-kitchenowl-reference-2026-05-10.md.

Run with:  flask --app app shell < scripts/seed_data.py
   or:     python scripts/seed_data.py  (with the app context)
"""

import sqlite3
from datetime import datetime
from database import get_db


def seed():
    db = get_db()
    cur = db.cursor()
    now = datetime.now().isoformat()

    # Clear any previously seeded data
    cur.execute("DELETE FROM recipe_ingredients")
    cur.execute("DELETE FROM recipes")
    cur.execute("DELETE FROM ingredients")
    db.commit()

    # --- INGREDIENTS ---
    # (name, display_name, category, unit, price, store, minimum_stock, pack_size)
    ingredients = [
        # Dairy & refrigerated
        ("Woolworths Free From Lactose Full Cream Long Life Milk UHT 1L",
         "Milk (Lactose Free, Full Cream)", "dairy", "L", 2.10, "Woolworths", 2.0, 1.0),
        ("Woolworths Free From Lactose Lite Long Life Milk UHT 1L",
         "Milk (Lactose Free, Lite)", "dairy", "L", 2.10, "Woolworths", 2.0, 1.0),
        ("Liddells Lactose Free Shredded Cheese Pizza Blend 225g",
         "Shredded Cheese (Pizza Blend)", "dairy", "kg", 30.00, "Woolworths", 0.5, 0.225),
        ("Liddells Lactose Free Tasty Cheese Slices 250g",
         "Cheese Slices (Lactose Free)", "dairy", "each", 6.75, "Woolworths", 2.0, 0.25),
        ("Bulla Lactose Free Sour Cream Light 200g",
         "Sour Cream (Lactose Free)", "dairy", "each", 4.50, "Woolworths", 1.0, 0.2),
        ("Nuttelex Buttery Table Spread 500g",
         "Table Spread (Nuttelex)", "dairy", "kg", 11.40, "Woolworths", 1.0, 0.5),
        ("Liddells Lactose Free Yoghurt Strawberry 140g x 4 pack",
         "Yoghurt (Lactose Free, Strawberry)", "dairy", "each", 6.40, "Woolworths", 2.0, 0.56),
        ("Liddells Lactose Free Yoghurt Plain 140g x 4 pack",
         "Yoghurt (Lactose Free, Plain)", "dairy", "each", 6.40, "Woolworths", 2.0, 0.56),
        ("Woolworths 6 Extra Large Free Range Eggs 350g",
         "Eggs", "dairy", "each", 4.90, "Woolworths", 2.0, 0.35),

        # Bakery
        ("Tip Top The One Wholemeal Bread Sandwich Slice Loaf Bakery 700g",
         "Bread (Wholemeal)", "bakery", "each", 4.60, "Woolworths", 2.0, 0.7),
        ("Tip Top Raisin Toast Loaf 500g",
         "Raisin Toast", "bakery", "each", 4.50, "Woolworths", 1.0, 0.5),
        ("Golden Crumpet Squares 6 pack",
         "Crumpets", "bakery", "each", 3.50, "Woolworths", 1.0, 0.3),
        ("Woolworths Pikelets 8 pack",
         "Pikelets", "bakery", "each", 2.50, "Woolworths", 1.0, 0.25),
        ("Woolworths English Muffins 6 pack",
         "English Muffins", "bakery", "each", 2.90, "Woolworths", 1.0, 0.3),

        # Meat & deli
        ("Woolworths Pork & Beef Mince 500g",
         "Mince (Pork & Beef)", "meat", "kg", 12.50, "Woolworths", 1.0, 0.5),
        ("D'Orsogna Middle Bacon From the Deli per 120g",
         "Bacon (Middle, Deli)", "meat", "kg", 158.33, "Woolworths", 0.5, 0.12),
        ("Ingham's Frozen Chicken Breast Tenders Southern Style 400g",
         "Chicken Breast Tenders", "meat", "kg", 11.25, "Woolworths", 1.0, 0.4),
        ("Ingham's Chicken Sliced From The Deli per 100g",
         "Chicken (Sliced, Deli)", "meat", "kg", 130.00, "Woolworths", 0.5, 0.1),
        ("KRC Mild Hungarian Salami Shaved From The Deli per 100g",
         "Salami (Mild Hungarian, Deli)", "meat", "kg", 300.00, "Woolworths", 0.3, 0.1),

        # Produce
        ("Cavendish Bananas each",
         "Bananas", "produce", "each", 0.81, "Woolworths", 5.0, 0.12),
        ("Woolworths Royal Gala Apple Punnet 1kg",
         "Apples (Royal Gala)", "produce", "kg", 4.90, "Woolworths", 2.0, 1.0),
        ("Capsicum Green each",
         "Capsicum (Green)", "produce", "each", 1.98, "Woolworths", 3.0, 0.15),
        ("The Odd Bunch Continental Cucumber each",
         "Cucumber", "produce", "each", 0.00, "Woolworths", 3.0, 0.4),
        ("Onion Brown each",
         "Onion (Brown)", "produce", "kg", 3.15, "Woolworths", 2.0, 0.5),
        ("The Odd Bunch Carrots 1.5kg",
         "Carrots", "produce", "kg", 1.87, "Woolworths", 2.0, 1.5),
        ("Woolworths Mixed Leaf Salad 200g",
         "Mixed Leaf Salad", "produce", "each", 3.00, "Woolworths", 2.0, 0.2),
        ("Woolworths Royal Gala Apple Punnet 1kg",
         "Apples (Royal Gala)", "produce", "kg", 4.90, "Woolworths", 2.0, 1.0),

        # Pantry
        ("Leggo's Bacon Flavour Bolognese Chunky Tomato & Herbs Pasta Sauce 500g",
         "Pasta Sauce (Bolognese)", "pantry", "each", 3.30, "Woolworths", 2.0, 0.5),
        ("Woolworths Lentils 420g",
         "Lentils (Canned)", "pantry", "each", 0.95, "Woolworths", 2.0, 0.42),
        ("Bell Farms Frozen Mash Potato 1kg",
         "Mashed Potato (Frozen)", "pantry", "kg", 5.00, "Woolworths", 1.0, 1.0),
        ("Woolworths Frozen Australian Broccoli Florets 500g",
         "Broccoli (Frozen)", "pantry", "kg", 8.00, "Woolworths", 1.0, 0.5),
        ("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g",
         "Mixed Vegetables (Frozen)", "pantry", "kg", 9.00, "Woolworths", 1.0, 0.5),
        ("Mission Wraps Spinach & Herb 8 pack",
         "Wraps (Spinach & Herb)", "pantry", "each", 5.60, "Woolworths", 1.0, 0.4),
        ("Green's Maple Syrup Pancake Shake 325g",
         "Maple Syrup", "pantry", "each", 3.80, "Woolworths", 1.0, 0.325),
    ]

    ingredient_ids = {}
    for ing in ingredients:
        name, display_name, category, unit, price, store, min_stock, pack_size = ing
        cur.execute(
            """INSERT INTO ingredients (name, display_name, category, unit, price, store,
                   minimum_stock, pack_size, created_at, origin)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'purchase_history')""",
            (name, display_name, category, unit, price, store, min_stock, pack_size, now)
        )
        ingredient_ids[name] = cur.lastrowid

    db.commit()
    print(f"Inserted {len(ingredients)} ingredients")

    # --- RECIPES ---
    recipes = [
        # (name, description, servings, covers_days, tags, [(ingredient_name, qty, unit), ...])
        ("Beef Burgers", "Family beef burgers with cheese, served on buns", 4, 1,
         "dinner,quick",
         [("Woolworths Pork & Beef Mince 500g", 0.5, "kg"),
          ("Liddells Lactose Free Tasty Cheese Slices 250g", 4, "each"),
          ("Tip Top The One Wholemeal Bread Sandwich Slice Loaf Bakery 700g", 4, "each"),
          ("Onion Brown each", 0.5, "each")]),
        ("Beef Stew", "Hearty beef stew with vegetables", 6, 2,
         "dinner,covers-2-days",
         [("Woolworths Pork & Beef Mince 500g", 0.5, "kg"),
          ("The Odd Bunch Carrots 1.5kg", 4, "each"),
          ("Onion Brown each", 1, "each"),
          ("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g", 0.5, "kg")]),
        ("Mac and Cheese", "Baked macaroni cheese with shredded cheese", 4, 1,
         "dinner,quick",
         [("Liddells Lactose Free Shredded Cheese Pizza Blend 225g", 225, "g"),
          ("Woolworths Free From Lactose Full Cream Long Life Milk UHT 1L", 0.5, "L"),
          ("Nuttelex Buttery Table Spread 500g", 50, "g")]),
        ("Pasta Bake", "Pasta bake with bolognese sauce and cheese", 4, 1, "dinner",
         [("Leggo's Bacon Flavour Bolognese Chunky Tomato & Herbs Pasta Sauce 500g", 500, "g"),
          ("Liddells Lactose Free Shredded Cheese Pizza Blend 225g", 225, "g"),
          ("Woolworths Pork & Beef Mince 500g", 0.25, "kg")]),
        ("Schnitzel and Chips", "Chicken breast tenders with frozen chips and salad", 4, 1,
         "dinner,quick",
         [("Ingham's Frozen Chicken Breast Tenders Southern Style 400g", 400, "g"),
          ("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g", 0.5, "kg"),
          ("Woolworths Mixed Leaf Salad 200g", 1, "each")]),
        ("Tuna Casserole", "Tuna pasta casserole with vegetables", 4, 1, "dinner",
         [("Leggo's Bacon Flavour Bolognese Chunky Tomato & Herbs Pasta Sauce 500g", 500, "g"),
          ("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g", 0.5, "kg"),
          ("Nuttelex Buttery Table Spread 500g", 30, "g")]),
        ("Pies and Veg", "Party pies and sausage rolls with mixed vegetables", 4, 1,
         "dinner,quick",
         [("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g", 0.5, "kg")]),
        ("Chicken Burgers", "Chicken breast burgers with cheese and salad", 4, 1,
         "dinner,quick",
         [("Ingham's Frozen Chicken Breast Tenders Southern Style 400g", 400, "g"),
          ("Liddells Lactose Free Tasty Cheese Slices 250g", 4, "each"),
          ("Tip Top The One Wholemeal Bread Sandwich Slice Loaf Bakery 700g", 4, "each"),
          ("Woolworths Mixed Leaf Salad 200g", 1, "each")]),
        ("Baked Potatoes with Coleslaw", "Baked potatoes with sour cream and coleslaw", 4, 1,
         "dinner",
         [("Bell Farms Frozen Mash Potato 1kg", 1, "kg"),
          ("Bulla Lactose Free Sour Cream Light 200g", 200, "g"),
          ("Woolworths Mixed Leaf Salad 200g", 1, "each")]),
        ("Spaghetti", "Spaghetti bolognese with mince and pasta sauce", 4, 1,
         "dinner,quick",
         [("Woolworths Pork & Beef Mince 500g", 0.5, "kg"),
          ("Leggo's Bacon Flavour Bolognese Chunky Tomato & Herbs Pasta Sauce 500g", 500, "g"),
          ("Onion Brown each", 1, "each")]),
        ("Stir Fry", "Chicken stir fry with mixed vegetables", 4, 1, "dinner,quick",
         [("Ingham's Frozen Chicken Breast Tenders Southern Style 400g", 400, "g"),
          ("McCain Frozen Mixed Vegetables Peas Corn & Carrot 500g", 0.5, "kg"),
          ("Onion Brown each", 1, "each")]),
        ("Tacos", "Beef tacos with cheese, wraps and salad", 4, 1, "dinner,quick",
         [("Woolworths Pork & Beef Mince 500g", 0.5, "kg"),
          ("Liddells Lactose Free Tasty Cheese Slices 250g", 4, "each"),
          ("Mission Wraps Spinach & Herb 8 pack", 8, "each"),
          ("Woolworths Mixed Leaf Salad 200g", 1, "each")]),
        ("Curry", "Mild curry with lentils and rice", 4, 1, "dinner,lunch",
         [("Woolworths Lentils 420g", 420, "g"),
          ("Onion Brown each", 1, "each"),
          ("Nuttelex Buttery Table Spread 500g", 30, "g")]),
        ("Pancakes", "Fluffy pancakes with maple syrup", 4, 1, "breakfast,quick",
         [("Woolworths Free From Lactose Full Cream Long Life Milk UHT 1L", 0.5, "L"),
          ("Woolworths 6 Extra Large Free Range Eggs 350g", 2, "each"),
          ("Nuttelex Buttery Table Spread 500g", 30, "g"),
          ("Green's Maple Syrup Pancake Shake 325g", 50, "g")]),
    ]

    for name, description, servings, covers_days, tags, recipe_ings in recipes:
        cur.execute(
            """INSERT INTO recipes (name, description, servings, covers_days, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, description, servings, covers_days, tags, now)
        )
        recipe_id = cur.lastrowid

        for ing_name, quantity, unit_override in recipe_ings:
            if ing_name not in ingredient_ids:
                print(f"  WARNING: Ingredient '{ing_name}' not found for recipe '{name}'")
                continue
            ingredient_id = ingredient_ids[ing_name]
            cur.execute(
                """INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override)
                   VALUES (?, ?, ?, ?)""",
                (recipe_id, ingredient_id, quantity, unit_override)
            )

    db.commit()
    print(f"Inserted {len(recipes)} recipes with ingredient links")
    print("\nDone! Seed complete.")


if __name__ == "__main__":
    seed()
