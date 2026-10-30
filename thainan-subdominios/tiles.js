/* Painel do Nonve: tiles do Umbrel abrem o endereço público (NetBird) do app. */
(function () {
  var MAPA = __MAPA__;
  var LOCAL = "nonve.local";
  function mapear(url) {
    try {
      var u = new URL(url, location.href);
      if (u.hostname === location.hostname && u.port && u.port !== location.port) {
        var pub = MAPA[u.port];
        return pub ? "https://" + pub + u.pathname + u.search + u.hash
                   : "http://" + LOCAL + ":" + u.port + u.pathname + u.search + u.hash;
      }
    } catch (e) {}
    return url;
  }
  var abrir = window.open;
  window.open = function (url, alvo, opts) {
    return abrir.call(window, url == null ? url : mapear(String(url)), alvo, opts);
  };
  /* links <a href="host:porta"> (clique, clique do meio, Ctrl+clique) */
  function corrigeLink(ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    if (!a) return;
    var novo = mapear(a.href);
    if (novo !== a.href) a.href = novo;
  }
  ["click", "auxclick", "mousedown", "contextmenu"].forEach(function (t) {
    document.addEventListener(t, corrigeLink, true);
  });
  try {
    var assign = Location.prototype.assign;
    Location.prototype.assign = function (url) { return assign.call(this, mapear(String(url))); };
  } catch (e) {}
})();
