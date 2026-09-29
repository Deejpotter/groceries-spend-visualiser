// Grocery Visualiser - shared client-side behaviour

document.addEventListener('DOMContentLoaded', function () {
    // Auto-dismiss success/info flash messages; keep errors visible until closed.
    setTimeout(function () {
        document.querySelectorAll('.alert-dismissible.alert-success, .alert-dismissible.alert-info')
            .forEach(function (el) { el.remove(); });
    }, 5000);

    // Shopping list: tick items in place so the page (and your scroll position) doesn't reload.
    // Without JS the checkbox still sits in a normal POST form with a CSRF token.
    document.querySelectorAll('.js-toggle-form').forEach(function (form) {
        var box = form.querySelector('input[type=checkbox]');
        box.addEventListener('change', function () {
            var item = form.closest('li');
            fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin'
            }).then(function (resp) {
                if (!resp.ok) { throw new Error(resp.status); }
                return resp.json();
            }).then(function (data) {
                box.checked = data.checked;
                item.classList.toggle('checked-item', data.checked);
                item.classList.toggle('list-group-item-light', data.checked);
                updateShopProgress();
            }).catch(function () {
                form.submit();
            });
        });
    });
});

function updateShopProgress() {
    var progress = document.querySelector('.shop-progress');
    if (!progress) { return; }
    var total = parseInt(progress.dataset.total, 10) || 0;
    var checked = document.querySelectorAll('.js-toggle-form input:checked').length;
    var pct = total ? Math.round(100 * checked / total) : 0;
    progress.querySelector('.js-checked-count').textContent = checked;
    progress.querySelector('.js-checked-pct').textContent = pct + '%';
    var bar = progress.querySelector('.progress-bar');
    bar.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', checked);
}
