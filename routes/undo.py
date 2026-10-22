"""Restore recently deleted items (undo)."""

from flask import Blueprint, flash, redirect, request, url_for

from auth import login_required
from database import get_db
from services import undo as undo_service

undo_bp = Blueprint("undo", __name__)

BACK_TARGETS = {
    "ingredient": "ingredients.ingredient_list",
    "recipe": "recipes.recipe_list",
    "pantry": "pantry.pantry_list",
}


@undo_bp.route("/undo/<int:token>", methods=["POST"])
@login_required
def undo_delete(token):
    kind = request.form.get("kind", "")
    target = BACK_TARGETS.get(kind, "dashboard")
    db = get_db()
    result = undo_service.restore(db, token)
    db.commit()
    if not result:
        flash("That delete can no longer be undone.", "warning")
    else:
        restored_kind, label = result
        flash("Restored {}.".format(label or restored_kind), "success")
    return redirect(url_for(target))


def register_undo_routes(app):
    app.register_blueprint(undo_bp)
