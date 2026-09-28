"""Register all route blueprints with the Flask app."""

from routes.ingredients import register_ingredient_routes
from routes.recipes import register_recipe_routes
from routes.meal_plan import register_meal_routes
from routes.shopping_list import register_shopping_routes
from routes.pantry import register_pantry_routes
from routes.settings import register_settings_routes
from routes.spend import register_spend_routes


def register_all_routes(app):
    """Register all application route blueprints."""
    register_ingredient_routes(app)
    register_recipe_routes(app)
    register_meal_routes(app)
    register_shopping_routes(app)
    register_pantry_routes(app)
    register_settings_routes(app)
    register_spend_routes(app)
