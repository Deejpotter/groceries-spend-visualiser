# HTMX Async Conversion Plan

## Slice 1: Foundation + Shopping List Tick (Proof of Concept)
1. Download and save htmx.min.js to static/js/
2. Add CSRF meta tag to base.html
3. Add global HTMX config script to base.html
4. Create partial template for shopping list item row
5. Modify shopping list tick route to return partial on HTMX request
6. Update shopping list view.html form to use hx-post/hx-target/hx-swap
7. Test: tick without page reload

## Slice 2: Pantry Operations
1. Create partial templates for pantry item rows and form status
2. Convert pantry consume/add/delete actions to HTMX
3. Update pantry view templates
4. Test: pantry operations without reload

## Slice 3: Subscriptions
1. Create partial template for subscription rows
2. Convert mark_paid/pause/resume/delete actions to HTMX
2. Update subscriptions view templates
3. Test: subscription operations without reload

## Slice 4: Settings
1. Create partial template for save status
2. Convert all settings forms to HTMX
3. Test: settings saves without redirect

## Slice 5: Product Linking
1. Create partial template for product link status
2. Convert product link actions to HTMX
3. Test: link status updates inline

## Slice 6: Meal Plan
1. Create partial templates for meal plan grid and actions
2. Convert add/swap/remove actions to HTMX
3. Test: meal plan updates without reload

## Slice 7: Documentation
1. Add HTMX architecture section to README.md
2. Update any relevant docs