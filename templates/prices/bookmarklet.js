(function () {
  var app = {{ app_url|tojson }};
  var host = location.hostname;
  var store = /woolworths\.com\.au$/.test(host) ? 'woolworths' : /coles\.com\.au$/.test(host) ? 'coles'
    : /aldi\.com\.au$/.test(host) ? 'aldi' : null;
  if (!store) { alert('Open a Woolworths, Coles or Aldi page first, then click this bookmark.'); return; }
  var data = {v: 1, store: store, url: location.href.split('#')[0], items: []};
  var seen = {};
  function walk(o, depth) {
    if (!o || typeof o !== 'object' || depth > 9) return;
    if (Array.isArray(o)) { for (var i = 0; i < o.length; i++) walk(o[i], depth + 1); return; }
    if (store === 'coles' && o.id && o.pricing && o.name) {
      if (!seen[o.id]) {
        seen[o.id] = 1;
        var u = o.pricing.unit || {};
        data.items.push({_type: o._type, id: o.id, name: o.name, brand: o.brand, size: o.size,
          availability: o.availability, pricing: {now: o.pricing.now, was: o.pricing.was,
          unit: {price: u.price, ofMeasureQuantity: u.ofMeasureQuantity, ofMeasureUnits: u.ofMeasureUnits}}});
      }
      return;
    }
    if (store === 'woolworths' && o.Stockcode && 'Price' in o) {
      if (!seen[o.Stockcode]) {
        seen[o.Stockcode] = 1;
        data.items.push({Stockcode: o.Stockcode, DisplayName: o.DisplayName, Name: o.Name, Price: o.Price,
          WasPrice: o.WasPrice, CupPrice: o.CupPrice, CupMeasure: o.CupMeasure, HasCupPrice: o.HasCupPrice,
          UrlFriendlyName: o.UrlFriendlyName, IsAvailable: o.IsAvailable});
      }
      return;
    }
    for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) walk(o[k], depth + 1); }
  }
  function code() {
    var m = store === 'woolworths' ? /\/productdetails\/(\d+)/.exec(location.pathname)
      : store === 'coles' ? /\/product\/[a-z0-9-]*?-(\d+)$/.exec(location.pathname) : null;
    return m ? m[1] : null;
  }
  function only(c) {
    var key = store === 'coles' ? 'id' : 'Stockcode';
    return data.items.filter(function (i) { return String(i[key]) === c; });
  }
  function send() {
    var w = window.open(app + '/prices/capture#' + encodeURIComponent(JSON.stringify(data)), '_blank');
    if (!w) alert('Your browser blocked the new tab. Allow pop-ups for this site and try again.');
  }
  if (store === 'aldi') {
    var html = [];
    document.querySelectorAll('.product-tile').forEach(function (t) { html.push(t.outerHTML.replace(/<img[^>]*>|<svg[\s\S]*?<\/svg>/g, '')); });
    if (!html.length) {
      document.querySelectorAll('script[type="application/ld+json"], .product-details__title, .product-details__brand-name, '
        + '.product-details__comparison-price').forEach(function (e) { html.push(e.outerHTML); });
    }
    data.html = html.join('\n');
    return send();
  }
  var r = window.next && window.next.router;
  var current = r && r.components && r.components[r.route];
  var nd = window.__NEXT_DATA__;
  if (store === 'coles' && nd) data.build = nd.buildId;
  walk(current && current.props ? current.props : nd && nd.props, 0);
  var c = code();
  if (!c) return send();
  data.items = only(c);
  if (data.items.length) return send();
  var url = store === 'woolworths' ? '/apis/ui/product/detail/' + c
    : '/_next/data/' + data.build + '/en' + location.pathname + '.json';
  fetch(url, {credentials: 'include'}).then(function (res) { return res.json(); })
    .then(function (json) { walk(json, 0); data.items = only(c); send(); })
    .catch(function () { send(); });
})();
