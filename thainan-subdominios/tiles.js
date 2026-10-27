/* Painel do Nonve: tiles do Umbrel abrem o endereço público (NetBird) do app. */
(function () {
  var MAPA = __MAPA__;
  var LOCAL = "nonve.local";
  var abrir = window.open;
  window.open = function (url, alvo, opts) {
    try {
      var u = new URL(url, location.href);
      if (u.hostname === location.hostname && u.port) {
        var pub = MAPA[u.port];
        url = pub ? "https://" + pub + u.pathname + u.search + u.hash
                  : "http://" + LOCAL + ":" + u.port + u.pathname + u.search + u.hash;
      }
    } catch (e) {}
    return abrir.call(window, url, alvo, opts);
  };
})();
