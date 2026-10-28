// Radar de Repos (Nonve) — serve a página Open UI, os dados e a fila de instalação. Sem dependências.
import http from "node:http"; import fs from "node:fs"; import path from "node:path"; import crypto from "node:crypto";
const DATA = process.env.DATA_DIR || "/data", WEB = process.env.WEB_DIR || "/app/web", PORT = +(process.env.PORT || 8561);
const TOKEN_SHA = (process.env.WRITE_TOKEN_SHA256 || "").toLowerCase();
fs.mkdirSync(DATA, { recursive: true });
const F = (n) => path.join(DATA, n);
const ler = (n, d) => { try { return JSON.parse(fs.readFileSync(F(n), "utf8")); } catch { return d; } };
const gravar = (n, v) => { fs.writeFileSync(F(n) + ".tmp", typeof v === "string" ? v : JSON.stringify(v, null, 1)); fs.renameSync(F(n) + ".tmp", F(n)); };
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
const enviar = (res, code, body, tipo = "application/json; charset=utf-8") => { res.writeHead(code, { "Content-Type": tipo, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }); res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body)); };
const corpo = (req, max = 2e6) => new Promise((ok, no) => { let b = ""; req.on("data", (c) => { b += c; if (b.length > max) { no(new Error("grande")); req.destroy(); } }); req.on("end", () => { try { ok(b ? JSON.parse(b) : {}); } catch (e) { no(e); } }); });
const autorizado = (req) => { const t = (req.headers.authorization || "").replace(/^Bearer\s+/i, ""); if (!TOKEN_SHA || !t) return false; const h = crypto.createHash("sha256").update(t).digest("hex"); return crypto.timingSafeEqual(Buffer.from(h), Buffer.from(TOKEN_SHA.padEnd(64, "0").slice(0, 64))); };
let ultimoPedido = {};
http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x"); const p = u.pathname.replace(/\/+$/, "") || "/";
  try {
    if (req.method === "GET" && p === "/api/radar") return enviar(res, 200, ler("hoje.json", { gerado: "", periodos: {} }));
    if (req.method === "GET" && p === "/api/ui") { let t = ""; try { t = fs.readFileSync(F("ui.oui"), "utf8"); } catch {} return enviar(res, 200, t, "text/plain; charset=utf-8"); }
    if (req.method === "GET" && p === "/api/fila") return enviar(res, 200, { pedidos: ler("fila.json", []) });
    if (req.method === "GET" && p === "/api/saude") return enviar(res, 200, { ok: true, gerado: ler("hoje.json", {}).gerado || null, pendentes: ler("fila.json", []).filter((x) => x.status === "pendente").length });
    if (req.method === "POST" && p === "/api/instalar") {
      const b = await corpo(req, 4000); const repo = String(b.repo || "").trim(); const acao = b.acao === "usar" ? "usar" : "instalar";
      const hoje = ler("hoje.json", { periodos: {} }); const item = Object.values(hoje.periodos).flat().find((r) => r.repo.toLowerCase() === repo.toLowerCase());
      if (!item) return enviar(res, 400, { erro: "repo fora do radar de hoje" });
      if (!["útil", "muito útil"].includes(item.veredito)) return enviar(res, 400, { erro: "só repos úteis" });
      const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress; const agora = Date.now();
      if (agora - (ultimoPedido[ip] || 0) < 1500) return enviar(res, 429, { erro: "calma" }); ultimoPedido[ip] = agora;
      const fila = ler("fila.json", []);
      const aberto = fila.find((x) => x.repo.toLowerCase() === repo.toLowerCase() && ["pendente", "em_andamento", "feito"].includes(x.status));
      if (aberto) return enviar(res, 200, { ok: true, pedido: aberto, repetido: true });
      const pedido = { id: crypto.randomUUID().slice(0, 8), repo: item.repo, acao, tipo: String(b.tipo || "").slice(0, 60), onde: item.onde, link: item.link, pedido_em: new Date().toISOString(), status: "pendente" };
      fila.push(pedido); gravar("fila.json", fila.slice(-300));
      return enviar(res, 200, { ok: true, pedido });
    }
    if (req.method === "POST" && p === "/api/radar") {
      if (!autorizado(req)) return enviar(res, 401, { erro: "token" });
      const b = await corpo(req, 3e6);
      if (!b.hoje || !b.hoje.periodos || typeof b.ui !== "string") return enviar(res, 400, { erro: "esperado {hoje, ui}" });
      gravar("hoje.json", b.hoje); gravar("ui.oui", b.ui); return enviar(res, 200, { ok: true });
    }
    if (req.method === "POST" && p === "/api/fila/status") {
      if (!autorizado(req)) return enviar(res, 401, { erro: "token" });
      const b = await corpo(req, 8000); const fila = ler("fila.json", []); const x = fila.find((y) => y.id === b.id);
      if (!x || !["pendente", "em_andamento", "feito", "erro"].includes(b.status)) return enviar(res, 400, { erro: "id/status" });
      x.status = b.status; x.nota = String(b.nota || "").slice(0, 300); x.atualizado_em = new Date().toISOString(); gravar("fila.json", fila);
      return enviar(res, 200, { ok: true, pedido: x });
    }
    if (req.method !== "GET") return enviar(res, 405, { erro: "método" });
    let f = path.normalize(path.join(WEB, p === "/" ? "index.html" : p)); if (!f.startsWith(WEB)) return enviar(res, 403, "");
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(WEB, "index.html");
    return enviar(res, 200, fs.readFileSync(f), MIME[path.extname(f)] || "application/octet-stream");
  } catch (e) { return enviar(res, 500, { erro: String(e.message || e) }); }
}).listen(PORT, "0.0.0.0", () => console.log("radar na porta", PORT));
