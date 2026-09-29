// Grocery Visualiser - shared client-side behaviour

document.addEventListener('DOMContentLoaded', function () {
    // Auto-dismiss success/info flash messages; keep errors visible until closed.
    setTimeout(function () {
        document.querySelectorAll('.alert-dismissible.alert-success, .alert-dismissible.alert-info')
            .forEach(function (el) { el.remove(); });
    }, 5000);
});
