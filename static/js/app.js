// Grocery Visualiser - Client-side JavaScript

$(document).ready(function() {
    // Flash message auto-dismiss after 5 seconds
    setTimeout(function() {
        $('.alert-dismissible').remove();
    }, 5000);

    // Ingredient row add/remove for recipe form
    if ($('#add-ingredient').length) {
        $('#add-ingredient').click(function(e) {
            e.preventDefault();
            // This is handled by server-rendered form
            alert('Add ingredient functionality requires server-side support.');
        });
    }

    // Confirm dialogs for destructive actions
    $('a[onclick*="confirm("]').each(function() {
        // Already has inline confirm, leave as-is
    });
});
