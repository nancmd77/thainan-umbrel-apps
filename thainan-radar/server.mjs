// Radar de Repos (Nonve) — serve a página Open UI, os dados e a fila de instalação. Sem dependências.
import http from "node:http"; import fs from "node:fs"; import path from "node:path"; import crypto from "node:crypto"; import zlib from "node:zlib";
const DATA = process.env.DATA_DIR || "/data", WEB = process.env.WEB_DIR || "/app/web", PORT = +(process.env.PORT || 8561);
const TOKEN_SHA = (process.env.WRITE_TOKEN_SHA256 || "").toLowerCase();
fs.mkdirSync(DATA, { recursive: true });
const F = (n) => path.join(DATA, n);
const ler = (n, d) => { try { return JSON.parse(fs.readFileSync(F(n), "utf8")); } catch { return d; } };
const gravar = (n, v) => { fs.writeFileSync(F(n) + ".tmp", typeof v === "string" ? v : JSON.stringify(v, null, 1)); fs.renameSync(F(n) + ".tmp", F(n)); };
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
let REQ = null; const cacheGz = new Map();
const enviar = (res, code, body, tipo = "application/json; charset=utf-8", estatico = false) => {
  let buf = Buffer.isBuffer(body) ? body : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
  const h = { "Content-Type": tipo, "X-Content-Type-Options": "nosniff", "Vary": "Accept-Encoding" };
  if (estatico) { const tag = '"' + crypto.createHash("sha1").update(buf).digest("hex").slice(0, 16) + '"'; h.ETag = tag; h["Cache-Control"] = "public, max-age=300, stale-while-revalidate=86400"; if (REQ && REQ.headers["if-none-match"] === tag) { res.writeHead(304, h); return res.end(); } }
  else h["Cache-Control"] = "no-store";
  if (buf.length > 1024 && /gzip/.test((REQ && REQ.headers["accept-encoding"]) || "") && !/image\/png/.test(tipo)) {
    const k = estatico ? h.ETag : null; let gz = k && cacheGz.get(k); if (!gz) { gz = zlib.gzipSync(buf, { level: 6 }); if (k) cacheGz.set(k, gz); }
    buf = gz; h["Content-Encoding"] = "gzip";
  }
  h["Content-Length"] = buf.length; res.writeHead(code, h); res.end(buf);
};
const corpo = (req, max = 2e6) => new Promise((ok, no) => { let b = ""; req.on("data", (c) => { b += c; if (b.length > max) { no(new Error("grande")); req.destroy(); } }); req.on("end", () => { try { ok(b ? JSON.parse(b) : {}); } catch (e) { no(e); } }); });
const autorizado = (req) => { const t = (req.headers.authorization || "").replace(/^Bearer\s+/i, ""); if (!TOKEN_SHA || !t) return false; const h = crypto.createHash("sha256").update(t).digest("hex"); return crypto.timingSafeEqual(Buffer.from(h), Buffer.from(TOKEN_SHA.padEnd(64, "0").slice(0, 64))); };

/* ===== regras-core (cópia de radar-ui/src/regras-core.js: mudou lá, muda aqui) ===== */
const FONTES = ["GitHub", "Hacker News", "Product Hunt", "AlternativeTo", "Self-hosted"];
const ACOES_REGRA = ["salvar", "destacar", "instalar", "dispensar"];
const normR = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
const numR = (v) => { const n = parseInt(String(v ?? "").replace(/[^\d]/g, ""), 10); return Number.isFinite(n) ? n : 0; };
const ehAppR = (r) => r.tipo_item === "app" || /^(ph|alt)\//i.test(r.repo || "");
const temPer = (r, p) => r.origem === p || (r.periodos || []).includes(p);
const fontesDe = (r) => {
  const f = [];
  if (!ehAppR(r) && !temPer(r, "Product Hunt") && !temPer(r, "AlternativeTo")) f.push("GitHub");
  if ((r.hn_pontos || 0) > 0 || temPer(r, "Hacker News")) f.push("Hacker News");
  for (const o of ["Product Hunt", "AlternativeTo", "Self-hosted"]) if (temPer(r, o)) f.push(o);
  return f;
};
// junta o mesmo item que aparece em vários períodos
function juntarR(periodos) {
  const m = new Map();
  for (const [per, itens] of Object.entries(periodos || {})) for (const r of itens || []) {
    if (!r || !r.repo) continue;
    const k = r.repo.toLowerCase(); const a = m.get(k);
    if (!a) { m.set(k, { ...r, k, periodos: [per], porque: [...(r.porque || [])] }); continue; }
    a.periodos.push(per);
    if ((r.nota || 0) > (a.nota || 0)) for (const f of ["nota", "veredito", "onde", "desc"]) if (r[f] != null) a[f] = r[f];
    for (const p of r.porque || []) if (!a.porque.includes(p)) a.porque.push(p);
    a.hn_pontos = Math.max(a.hn_pontos || 0, r.hn_pontos || 0);
    a.estrelas_semana = Math.max(numR(a.estrelas_semana), numR(r.estrelas_semana));
    a.origem = a.origem || r.origem; a.github = a.github || r.github; a.link = a.link || r.link;
    if (r.afinidade != null) a.afinidade = Math.max(a.afinidade ?? -1, r.afinidade);
  }
  return [...m.values()];
}
const palavrasDe = (txt) => String(txt || "").split(/[,;\n]+/).map((w) => w.trim()).filter(Boolean).slice(0, 12);
// uma regra pega o item quando TODAS as condições preenchidas batem; entre palavras basta uma
function casa(regra, r) {
  const q = regra.quando || {};
  const pal = (q.palavras || []).map(normR).filter(Boolean);
  if (pal.length) { const h = normR([r.repo, r.nome, r.desc, (r.porque || []).join(" ")].join(" ")); if (!pal.some((p) => h.includes(p))) return false; }
  if (q.fontes && q.fontes.length) { const f = fontesDe(r); if (!q.fontes.some((x) => f.includes(x))) return false; }
  if (q.tipo === "repo" && ehAppR(r)) return false;
  if (q.tipo === "app" && !ehAppR(r)) return false;
  if (q.veredito === "util" && !["útil", "muito útil"].includes(r.veredito)) return false;
  if (q.veredito === "muito_util" && r.veredito !== "muito útil") return false;
  if (q.afinidade_alta && (r.afinidade ?? 0) < 0.4) return false;
  if (q.min_semana > 0 && numR(r.estrelas_semana) <= q.min_semana) return false;
  return true;
}
// regra sem nenhuma condição pegaria tudo: não vale
const regraVazia = (q = {}) => !(q.palavras || []).length && !(q.fontes || []).length && !q.tipo && !q.veredito && !q.afinidade_alta && !(q.min_semana > 0);
const podeInstalar = (r) => ["útil", "muito útil"].includes(r.veredito) && !r.sua_skill && (!ehAppR(r) || /github\.com\//.test(r.github || ""));

/* ===== listas e regras ===== */
// ISO montado na mão: alguns ambientes mexem no toISOString
const agoraISO = (t = Date.now()) => { const d = new Date(t), z = (n, l = 2) => String(n).padStart(l, "0"); return `${d.getUTCFullYear()}-${z(d.getUTCMonth() + 1)}-${z(d.getUTCDate())}T${z(d.getUTCHours())}:${z(d.getUTCMinutes())}:${z(d.getUTCSeconds())}.${z(d.getUTCMilliseconds(), 3)}Z`; };
const LISTAS_INICIAIS = [["pra-testar", "Pra testar"], ["lume", "Lume"], ["nonve", "Nonve"], ["hark", "Hark"], ["edicao-de-video", "Edição de vídeo"], ["n8n", "n8n"], ["instagram", "Instagram"], ["igreja", "Igreja"]];
const REGRAS_INICIAIS = [
  { id: "self-hosted", nome: "Self-hosted ou docker vai pra Pra testar", ligada: false, quando: { palavras: ["self-hosted", "selfhosted", "self hosted", "docker"], fontes: [], tipo: null, veredito: null, afinidade_alta: false, min_semana: 0 }, entao: { acao: "salvar", lista: "pra-testar" } },
  { id: "video-util", nome: "Vídeo útil vai pra Edição de vídeo", ligada: false, quando: { palavras: ["video", "ffmpeg", "remotion", "davinci", "premiere", "subtitle"], fontes: [], tipo: null, veredito: "util", afinidade_alta: false, min_semana: 0 }, entao: { acao: "salvar", lista: "edicao-de-video" } },
  { id: "muito-util-em-alta", nome: "Muito útil e bombando ganha destaque", ligada: false, quando: { palavras: [], fontes: [], tipo: null, veredito: "muito_util", afinidade_alta: false, min_semana: 1000 }, entao: { acao: "destacar", lista: null } },
];
const REPO_RE = /^[\w.-]+\/[\w.-]+$/;
const txt = (v, n) => String(v ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, n);
const urlOk = (v) => (/^https?:\/\/[^\s"'<>]+$/i.test(String(v || "")) ? String(v).slice(0, 400) : null);
const slug = (s) => normR(s).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "lista";
const novoId = (base, usados) => { let id = slug(base), i = 2; while (usados.includes(id)) id = slug(base) + "-" + i++; return id; };
const lerListas = () => { const l = ler("listas.json", null); if (Array.isArray(l)) return l; const em = agoraISO(); return LISTAS_INICIAIS.map(([id, nome]) => ({ id, nome, criada_em: em, itens: [] })); };
const lerRegras = () => { const r = ler("regras.json", null); return Array.isArray(r) ? r : REGRAS_INICIAIS.map((x) => ({ ...x, criada_em: agoraISO() })); };
const vivas = (a) => a.filter((x) => !x.apagada_em);
const limparApagadas = (a) => a.filter((x) => !x.apagada_em || Date.now() - new Date(x.apagada_em).getTime() < 30 * 864e5);
const itensHoje = () => juntarR(ler("hoje.json", { periodos: {} }).periodos);
// retrato do item: o radar troca todo dia, então a lista guarda o que precisa pra se virar sozinha
function retrato(repo, d = {}, b = {}) {
  const app = ehAppR(d.repo ? d : { repo, tipo_item: b.tipo === "app" ? "app" : null });
  return { repo: d.repo || repo, nome: txt(d.nome || b.nome || repo.split("/")[1], 120), desc: txt(d.desc || b.desc, 400),
    link: urlOk(d.link) || urlOk(b.link) || (app ? urlOk(d.github) : "https://github.com/" + repo),
    origem: txt((d.repo ? fontesDe(d).filter((f) => f !== "GitHub")[0] || fontesDe(d)[0] : b.origem) || "GitHub", 40), tipo: app ? "app" : "repo" };
}
function limparRegra(b, listas, existente) {
  const q = b.quando || {}, e = b.entao || {};
  const quando = {
    palavras: (Array.isArray(q.palavras) ? q.palavras : []).map((w) => txt(w, 40)).filter(Boolean).slice(0, 12),
    fontes: (Array.isArray(q.fontes) ? q.fontes : []).filter((f) => FONTES.includes(f)),
    tipo: ["repo", "app"].includes(q.tipo) ? q.tipo : null,
    veredito: ["util", "muito_util"].includes(q.veredito) ? q.veredito : null,
    afinidade_alta: q.afinidade_alta === true,
    min_semana: Math.max(0, Math.min(1e6, parseInt(q.min_semana, 10) || 0)),
  };
  if (regraVazia(quando)) return { erro: "a regra precisa de pelo menos uma condição" };
  if (!ACOES_REGRA.includes(e.acao)) return { erro: "acao" };
  const lista = e.acao === "salvar" ? String(e.lista || "") : null;
  if (e.acao === "salvar" && !vivas(listas).some((l) => l.id === lista)) return { erro: "lista" };
  const nome = txt(b.nome, 80) || "Regra sem nome";
  return { regra: { ...(existente || {}), nome, ligada: b.ligada !== false, quando, entao: { acao: e.acao, lista } } };
}
const DIZ = { salvar: "salvei", destacar: "destaquei", instalar: "pedi a instalação", dispensar: "dispensei" };
// roda as regras ligadas (ou só uma, quando o nan pede) nos itens de hoje; não repete o que já fez
function aplicarRegras(hoje, so) {
  const regras = vivas(lerRegras()).filter((r) => (so ? r.id === so : r.ligada));
  if (!regras.length || !hoje || !hoje.periodos) return [];
  const itens = juntarR(hoje.periodos);
  const listas = lerListas(); const fila = ler("fila.json", []); const tri = ler("triagem.json", []);
  const log = ler("regras_log.json", { execucoes: [], feitos: {} }); log.feitos = log.feitos || {};
  const agora = agoraISO(); const pegos = []; const mud = {};
  for (const rg of regras) for (const r of itens) {
    if (!casa(rg, r)) continue;
    const chave = rg.id + "|" + r.k; if (log.feitos[chave]) continue;
    const ent = { repo: r.repo, nome: r.nome || r.repo.split("/")[1], regra: rg.id, regra_nome: rg.nome, acao: rg.entao.acao, lista: rg.entao.lista || null, ok: true };
    if (rg.entao.acao === "salvar") {
      const L = listas.find((l) => l.id === rg.entao.lista && !l.apagada_em);
      if (!L) { ent.ok = false; ent.nota = "a lista não existe mais"; }
      else if (L.itens.some((i) => i.repo.toLowerCase() === r.k)) { ent.ok = false; ent.nota = "já estava na lista"; }
      else { L.itens.unshift({ ...retrato(r.repo, r), salvo_em: agora, nota: "", por: "regra:" + rg.id }); mud.listas = 1; }
    } else if (rg.entao.acao === "instalar") {
      if (!podeInstalar(r)) { ent.ok = false; ent.nota = "não é útil o bastante, não pedi"; }
      else if (fila.some((x) => x.repo.toLowerCase() === r.k && ["pendente", "em_andamento", "feito"].includes(x.status))) { ent.ok = false; ent.nota = "já estava na fila"; }
      else { fila.push({ id: crypto.randomUUID().slice(0, 8), repo: r.repo, acao: "instalar", tipo: "Pela regra " + txt(rg.nome, 50), onde: r.onde, link: r.link, pedido_em: agora, status: "pendente", por: "regra:" + rg.id }); mud.fila = 1; }
    } else if (rg.entao.acao === "dispensar") {
      if (tri.some((t) => t.repo.toLowerCase() === r.k)) { ent.ok = false; ent.nota = "você já tinha triado"; }
      else { tri.push({ repo: r.repo, acao: "dispensar", em: agora, por: "regra:" + rg.id }); mud.tri = 1; }
    }
    log.feitos[chave] = agora; pegos.push(ent);
  }
  if (mud.listas) gravar("listas.json", listas);
  if (mud.fila) gravar("fila.json", fila.slice(-300));
  if (mud.tri) gravar("triagem.json", tri.slice(-2000));
  const ks = Object.keys(log.feitos); if (ks.length > 6000) for (const k of ks.slice(0, ks.length - 6000)) delete log.feitos[k];
  log.execucoes = [{ em: agora, gerado: hoje.gerado || null, motivo: so ? "manual" : "radar novo", regras: regras.map((r) => r.id), itens: pegos }, ...(log.execucoes || [])].slice(0, 40);
  gravar("regras_log.json", log);
  return pegos;
}
const pegosHoje = () => {
  const g = ler("hoje.json", {}).gerado || null; const log = ler("regras_log.json", { execucoes: [] }); const vistos = new Set(); const out = [];
  for (const ex of log.execucoes || []) if (ex.gerado === g) for (const i of ex.itens || []) { const k = i.regra + "|" + i.repo.toLowerCase(); if (i.ok && !vistos.has(k)) { vistos.add(k); out.push({ ...i, em: ex.em }); } }
  return out;
};
const respRegras = () => ({ ok: true, regras: vivas(lerRegras()), pegos: pegosHoje(), execucoes: (ler("regras_log.json", { execucoes: [] }).execucoes || []).slice(0, 10) });

let ultimoPedido = {};
http.createServer(async (req, res) => {
  REQ = req;
  const u = new URL(req.url, "http://x"); const p = u.pathname.replace(/\/+$/, "") || "/";
  try {
    if (req.method === "GET" && p === "/api/radar") return enviar(res, 200, ler("hoje.json", { gerado: "", periodos: {} }));
    if (req.method === "GET" && p === "/api/ui") { let t = ""; try { t = fs.readFileSync(F("ui.oui"), "utf8"); } catch {} return enviar(res, 200, t, "text/plain; charset=utf-8"); }
    if (req.method === "GET" && p === "/api/fila") return enviar(res, 200, { pedidos: ler("fila.json", []) });
    if (req.method === "GET" && p === "/api/saude") return enviar(res, 200, { ok: true, gerado: ler("hoje.json", {}).gerado || null, pendentes: ler("fila.json", []).filter((x) => x.status === "pendente").length, skills_pendentes: ler("skills_fila.json", []).filter((x) => x.status === "pendente").length });
    if (req.method === "GET" && p === "/api/avaliacoes") return enviar(res, 200, { avaliacoes: ler("avaliacoes.json", []) });
    if (req.method === "GET" && p === "/api/skills") return enviar(res, 200, ler("skills.json", { atualizado: null, itens: [], escopos: [] }));
    if (req.method === "GET" && p === "/api/skills/fila") return enviar(res, 200, { pedidos: ler("skills_fila.json", []) });
    if (req.method === "POST" && p === "/api/skills/pedido") {
      const b = await corpo(req, 4000); const sk = ler("skills.json", { itens: [], escopos: [] });
      const acao = String(b.acao || ""); const escopo = String(b.escopo || ""); const item = String(b.item || "");
      const ACOES = ["ativar", "desativar", "ativar_todas", "desativar_todas", "copiar_chat", "aplicar_todos"];
      if (!ACOES.includes(acao) || !sk.escopos.some((e) => e.id === escopo)) return enviar(res, 400, { erro: "acao/escopo" });
      if (["ativar", "desativar"].includes(acao) && !sk.itens.some((i) => i.nome === item)) return enviar(res, 400, { erro: "item" });
      const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress; const agora = Date.now();
      if (agora - (ultimoPedido["s" + ip] || 0) < 800) return enviar(res, 429, { erro: "calma" }); ultimoPedido["s" + ip] = agora;
      let fila = ler("skills_fila.json", []);
      // pedido novo pro mesmo item+escopo substitui o pendente anterior (o último clique vale)
      fila = fila.filter((x) => !(x.status === "pendente" && x.escopo === escopo && x.item === (item || null) && ["ativar", "desativar"].includes(x.acao) && ["ativar", "desativar"].includes(acao)));
      const pedido = { id: crypto.randomUUID().slice(0, 8), acao, escopo, item: item || null, pedido_em: new Date().toISOString(), status: "pendente" };
      fila.push(pedido); gravar("skills_fila.json", fila.slice(-300)); return enviar(res, 200, { ok: true, pedido });
    }
    if (req.method === "POST" && p === "/api/skills") {
      if (!autorizado(req)) return enviar(res, 401, { erro: "token" });
      const b = await corpo(req, 5e5); if (!Array.isArray(b.itens) || !Array.isArray(b.escopos)) return enviar(res, 400, { erro: "esperado {itens, escopos}" });
      gravar("skills.json", b); return enviar(res, 200, { ok: true });
    }
    if (req.method === "POST" && p === "/api/skills/fila/status") {
      if (!autorizado(req)) return enviar(res, 401, { erro: "token" });
      const b = await corpo(req, 8000); const fila = ler("skills_fila.json", []); const x = fila.find((y) => y.id === b.id);
      if (!x || !["pendente", "em_andamento", "feito", "erro"].includes(b.status)) return enviar(res, 400, { erro: "id/status" });
      x.status = b.status; x.nota = String(b.nota || "").slice(0, 300); x.atualizado_em = new Date().toISOString(); gravar("skills_fila.json", fila);
      return enviar(res, 200, { ok: true, pedido: x });
    }
    if (req.method === "POST" && p === "/api/avaliar") {
      const b = await corpo(req, 4000); const repo = String(b.repo || "").trim(); const voto = String(b.voto || "");
      if (!/^[\w.-]+\/[\w.-]+$/.test(repo) || !["serve", "nao_serve", "limpar"].includes(voto)) return enviar(res, 400, { erro: "repo/voto" });
      const hoje = ler("hoje.json", { periodos: {} }); const item = Object.values(hoje.periodos).flat().find((r) => r.repo.toLowerCase() === repo.toLowerCase()) || {};
      let lista = ler("avaliacoes.json", []).filter((a) => a.repo.toLowerCase() !== repo.toLowerCase());
      if (voto !== "limpar") lista.push({ repo: item.repo || repo, voto, veredito_radar: item.veredito || null, nota_radar: item.nota ?? null, onde: item.onde || null, desc: String(item.desc || "").slice(0, 300), lang: item.lang || null, em: new Date().toISOString() });
      gravar("avaliacoes.json", lista.slice(-1000)); return enviar(res, 200, { ok: true });
    }
    if (req.method === "GET" && p === "/api/listas") return enviar(res, 200, { listas: vivas(lerListas()) });
    if (req.method === "POST" && p === "/api/listas") {
      // criar, renomear, apagar (vai pra lixeira 30 dias, dá pra restaurar), salvar, tirar, mover, nota
      const b = await corpo(req, 6000); const acao = String(b.acao || ""); const ACS = ["criar", "renomear", "apagar", "restaurar", "salvar", "tirar", "mover", "nota"];
      if (!ACS.includes(acao)) return enviar(res, 400, { erro: "acao" });
      let listas = limparApagadas(lerListas()); const repo = String(b.repo || "").trim();
      if (["salvar", "tirar", "mover", "nota"].includes(acao) && !REPO_RE.test(repo)) return enviar(res, 400, { erro: "repo" });
      const achar = (id, todas) => listas.find((l) => l.id === String(id || "") && (todas || !l.apagada_em));
      let L = null;
      if (acao === "criar") {
        const nome = txt(b.nome, 60); if (!nome) return enviar(res, 400, { erro: "nome" });
        if (vivas(listas).length >= 60) return enviar(res, 400, { erro: "listas demais" });
        const igual = vivas(listas).find((l) => normR(l.nome) === normR(nome));
        L = igual || { id: novoId(nome, listas.map((l) => l.id)), nome, criada_em: agoraISO(), itens: [] }; if (!igual) listas.push(L);
        if (REPO_RE.test(repo) && !L.itens.some((i) => i.repo.toLowerCase() === repo.toLowerCase())) L.itens.unshift({ ...retrato(repo, itensHoje().find((r) => r.k === repo.toLowerCase()), b.item || {}), salvo_em: agoraISO(), nota: "", por: "voce" });
      } else {
        L = achar(b.lista, acao === "restaurar"); if (!L) return enviar(res, 400, { erro: "lista" });
        const idx = L.itens.findIndex((i) => i.repo.toLowerCase() === repo.toLowerCase());
        if (acao === "renomear") { const nome = txt(b.nome, 60); if (!nome) return enviar(res, 400, { erro: "nome" }); L.nome = nome; }
        else if (acao === "apagar") L.apagada_em = agoraISO();
        else if (acao === "restaurar") delete L.apagada_em;
        else if (acao === "salvar") {
          if (idx < 0) {
            if (L.itens.length >= 500) return enviar(res, 400, { erro: "lista cheia" });
            const it = { ...retrato(repo, itensHoje().find((r) => r.k === repo.toLowerCase()), b.item || {}), salvo_em: (!isNaN(Date.parse(b.salvo_em)) && b.salvo_em) || agoraISO(), nota: txt(b.nota, 280), por: /^regra:[\w-]+$/.test(b.por || "") ? b.por : "voce" };
            const pos = Number.isInteger(b.pos) ? Math.max(0, Math.min(L.itens.length, b.pos)) : 0; L.itens.splice(pos, 0, it);
          }
        }
        else if (idx < 0) return enviar(res, 400, { erro: "item fora da lista" });
        else if (acao === "tirar") L.itens.splice(idx, 1);
        else if (acao === "nota") L.itens[idx].nota = txt(b.nota, 280);
        else if (acao === "mover") {
          const P = achar(b.para); if (!P || P === L) return enviar(res, 400, { erro: "para" });
          const [it] = L.itens.splice(idx, 1); if (!P.itens.some((i) => i.repo.toLowerCase() === repo.toLowerCase())) P.itens.unshift(it);
        }
      }
      gravar("listas.json", listas); return enviar(res, 200, { ok: true, lista: L && { id: L.id, nome: L.nome }, listas: vivas(listas) });
    }
    if (req.method === "GET" && p === "/api/regras") return enviar(res, 200, respRegras());
    if (req.method === "POST" && p === "/api/regras") {
      // salvar (cria ou edita), ligar, apagar (lixeira 30 dias), restaurar, aplicar (roda uma regra agora nos itens de hoje)
      const b = await corpo(req, 6000); const acao = String(b.acao || "");
      if (!["salvar", "ligar", "apagar", "restaurar", "aplicar"].includes(acao)) return enviar(res, 400, { erro: "acao" });
      let regras = limparApagadas(lerRegras()); const id = String(b.id || (b.regra && b.regra.id) || "");
      const R = regras.find((r) => r.id === id);
      if (acao === "salvar") {
        if (vivas(regras).length >= 40 && !R) return enviar(res, 400, { erro: "regras demais" });
        const v = limparRegra(b.regra || {}, lerListas(), R); if (v.erro) return enviar(res, 400, { erro: v.erro });
        if (R) Object.assign(R, v.regra); else regras.push({ ...v.regra, id: novoId(v.regra.nome, regras.map((r) => r.id)), criada_em: agoraISO() });
      } else {
        if (!R || (acao !== "restaurar" && R.apagada_em)) return enviar(res, 400, { erro: "id" });
        if (acao === "ligar") R.ligada = b.ligada === true;
        else if (acao === "apagar") R.apagada_em = agoraISO();
        else if (acao === "restaurar") delete R.apagada_em;
      }
      gravar("regras.json", regras);
      let pegos = null; if (acao === "aplicar") pegos = aplicarRegras(ler("hoje.json", null), R.id);
      return enviar(res, 200, { ...respRegras(), aplicados: pegos });
    }
    if (req.method === "GET" && p === "/api/triagem") return enviar(res, 200, { itens: ler("triagem.json", []) });
    if (req.method === "POST" && p === "/api/triagem") {
      // dispensar some de vez, adiar some por 7 dias, desfazer devolve pra caixa
      const b = await corpo(req, 4000); const repo = String(b.repo || "").trim(); const acao = String(b.acao || "");
      if (!/^[\w.-]+\/[\w.-]+$/.test(repo) || !["dispensar", "adiar", "desfazer"].includes(acao)) return enviar(res, 400, { erro: "repo/acao" });
      let lista = ler("triagem.json", []).filter((t) => t.repo.toLowerCase() !== repo.toLowerCase());
      const agora = new Date(); let item = null;
      if (acao !== "desfazer") { item = { repo, acao, em: agora.toISOString() }; if (acao === "adiar") item.ate = new Date(agora.getTime() + 7 * 864e5).toISOString(); lista.push(item); }
      gravar("triagem.json", lista.slice(-2000)); return enviar(res, 200, { ok: true, item });
    }
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
      gravar("hoje.json", b.hoje); gravar("ui.oui", b.ui);
      let regras = []; try { regras = aplicarRegras(b.hoje); } catch (e) { console.log("regras falharam", e); }
      return enviar(res, 200, { ok: true, regras: regras.length });
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
    return enviar(res, 200, fs.readFileSync(f), MIME[path.extname(f)] || "application/octet-stream", !f.endsWith("index.html"));
  } catch (e) { return enviar(res, 500, { erro: String(e.message || e) }); }
}).listen(PORT, "0.0.0.0", () => console.log("radar na porta", PORT));
