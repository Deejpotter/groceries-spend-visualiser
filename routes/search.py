"""Global search across ingredients, recipes, products and bills."""

from flask import Blueprint, render_template, request

from auth import login_required
from database import get_db

search_bp = Blueprint("search", __name__)

PER_GROUP_LIMIT = 20


def run_search(query):
    """Return grouped results for a query. Empty query returns empty groups."""
    results = {"ingredients": [], "recipes": [], "products": [], "bills": []}
    if not query:
        return results

    like = "%{}%".format(query)
    db = get_db()
    results["ingredients"] = [dict(r) for r in db.execute(
        """SELECT id, name, display_name, category, unit FROM ingredients
           WHERE name LIKE ? OR display_name LIKE ?
           ORDER BY name LIMIT ?""",
        (like, like, PER_GROUP_LIMIT),
    )]
    results["recipes"] = [dict(r) for r in db.execute(
        """SELECT id, name, tags, servings FROM recipes
           WHERE name LIKE ? OR description LIKE ? OR tags LIKE ?
           ORDER BY name LIMIT ?""",
        (like, like, like, PER_GROUP_LIMIT),
    )]
    results["products"] = [dict(r) for r in db.execute(
        """SELECT product_name, COUNT(*) AS orders,
                  ROUND(SUM(line_total), 2) AS spend, MAX(ingredient_id) AS ingredient_id
           FROM purchases WHERE product_name LIKE ?
           GROUP BY product_name ORDER BY spend DESC LIMIT ?""",
        (like, PER_GROUP_LIMIT),
    )]
    results["bills"] = [dict(r) for r in db.execute(
        """SELECT id, name, amount, frequency, next_due, active FROM subscriptions
           WHERE name LIKE ? ORDER BY name LIMIT ?""",
        (like, PER_GROUP_LIMIT),
    )]
    return results


@search_bp.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = run_search(query)
    total = sum(len(rows) for rows in results.values())
    return render_template("search.html", query=query, results=results, total=total)


def register_search_routes(app):
    app.register_blueprint(search_bp)
