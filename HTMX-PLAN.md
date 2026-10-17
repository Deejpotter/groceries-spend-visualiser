# HTMX Async Conversion Plan — COMPLETE

All slices 1-7 implemented, tested, and merged to dev.

## Slice 1: Foundation + Shopping List Tick ✅ DONE
- htmx.min.js saved to static/js/
- CSRF meta tag + global HTMX config in base.html
- Created _item_row.html partial for shopping list rows
- Converted shopping list tick to hx-post/hx-target/hx-swap
- Route returns partial for HX-Request, falls back to normal redirect

## Slice 2: Pantry Operations ✅ DONE
- Created _item_row.html partial for pantry rows
- pantry_consume returns updated row partial, 204 when depleted
- pantry_delete returns 204 on HX-Request (row removed via HTMX)
- pantry_form returns 204 on HX-Request success
- list.html renders _item_row.html partial per row with hx-post/hx-target/hx-swap

## Slice 3: Subscriptions ✅ DONE
- Created _sub_row.html and _inactive_row.html partials
- mark_paid returns updated row partial on HX-Request
- toggle/pause returns 204 (row removed from active list)
- resume returns updated row partial on HX-Request
- delete (soft) and purge return 204 on HX-Request

## Slice 4: Settings ✅ DONE
- Each settings form (dates/prefs/bank/budget) uses hx-post/hx-target/hx-swap
- Success returns _save_status.html partial (no page reload)
- test_banksync returns inline success/error alert on HTMX
- Created _save_status.html partial

## Slice 5: Product Linking ✅ DONE
- Each product row links via HTMX on ingredient change (instant feedback)
- spend_link_product returns status partial on HX-Request
- Save all links button kept with HTMX batch submission

## Slice 6: Meal Plan ✅ DONE
- meal_plan_add/swap/remove/edit_servings return slot partials on HX-Request
- Created _slot_filled.html and _slot_empty.html partials
- _build_days adds servings_label to entries for aria labels

## Slice 7: Documentation ✅ DONE
- README.md: HTMX added to tech stack, async page updates section, project structure
- architecture.md: HTMX in tech stack, layered architecture diagram updated, dedicated HTMX section with pattern + partials table
