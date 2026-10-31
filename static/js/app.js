// Larder - shared client-side behaviour

document.addEventListener('DOMContentLoaded', function () {
    // Auto-dismiss success/info flash messages; keep errors visible until closed.
    setTimeout(function () {
        document.querySelectorAll('.alert-dismissible.alert-success, .alert-dismissible.alert-info')
            .forEach(function (el) { el.remove(); });
    }, 5000);

    // Theme toggle: persists in localStorage (pre-paint script in base.html applies it early).
    (function () {
        var btn = document.getElementById('theme-toggle');
        if (!btn) return;
        function paint() {
            var dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
            var use = btn.querySelector('use');
            if (use) use.setAttribute('href', dark ? '#i-sun' : '#i-moon');
        }
        btn.addEventListener('click', function () {
            var next = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-bs-theme', next);
            try { localStorage.setItem('gv-theme', next); } catch (e) {}
            paint();
            document.dispatchEvent(new CustomEvent('gv:themechange', { detail: { theme: next } }));
        });
        paint();
    })();

    // KPI numbers count up on load. The real value is already in the HTML, so no-JS still works.
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        document.querySelectorAll('.kpi-value').forEach(function (el) {
            var node = el.firstChild;
            var match = node && node.nodeType === 3 && node.nodeValue.match(/^(\s*\$?)([\d,]+(?:\.(\d+))?)([\s\S]*)$/);
            if (!match) { return; }
            var target = parseFloat(match[2].replace(/,/g, ''));
            var decimals = match[3] ? match[3].length : 0;
            var started = performance.now();
            (function frame(now) {
                var t = Math.min((now - started) / 900, 1);
                var value = target * (1 - Math.pow(1 - t, 3));
                node.nodeValue = match[1] + value.toLocaleString('en-AU', {
                    minimumFractionDigits: decimals, maximumFractionDigits: decimals
                }) + match[4];
                if (t < 1) { requestAnimationFrame(frame); }
                else { node.nodeValue = match[0]; }
            })(started);
        });
    }

    // Print: expand every disclosure first so packed/hidden options reach paper.
    window.addEventListener('beforeprint', function () {
        document.querySelectorAll('details:not([open])').forEach(function (d) {
            d.setAttribute('data-was-closed', '1');
            d.open = true;
        });
    });
    window.addEventListener('afterprint', function () {
        document.querySelectorAll('details[data-was-closed]').forEach(function (d) {
            d.open = false;
            d.removeAttribute('data-was-closed');
        });
    });

    // Generate buttons: disable with a spinner so a double-click can't double-POST.
    document.querySelectorAll('form[action$="/generate"]').forEach(function (form) {
        form.addEventListener('submit', function () {
            var btn = form.querySelector('button[type=submit]');
            if (btn && !btn.disabled) {
                btn.disabled = true;
                btn.insertAdjacentHTML('afterbegin',
                    '<span class="spinner-border spinner-border-sm mr-1" role="status" aria-hidden="true"></span>');
            }
        });
    });
    // Shopping list: tick items in place so the page (and your scroll position) doesn't reload.
    // Without JS each row has a Save button (noscript) submitting the same POST form.
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
