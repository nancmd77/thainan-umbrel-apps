"""Changelog do Artificial Analysis -> itens agrupados, sem redundância.

Regras:
- Artigo novo: 1 item por artigo.
- Avaliação de modelo: 1 item por modelo (slug /models/X) por dia, juntando as variantes
  (Low, Medium, High...) com o Intelligence Index de cada uma.
- "Performance results now available" (provedores) é ignorado: repete os mesmos modelos.
Cada item tem uma chave estável; a linha só muda se aparecer variante nova.
"""
import re
from lxml import html as LH

URL = "https://artificialanalysis.ai/changelog"
BASE = "https://artificialanalysis.ai"
ORDEM = ["minimal", "low", "medium", "high", "xhigh", "max"]


def _txt(el):
    return re.sub(r"\s+", " ", el.text_content()).strip()


def parse(page_html):
    doc = LH.fromstring(page_html)
    dia = None
    artigos, modelos, dias = [], {}, []
    for el in doc.iter("h4", "a"):
        if el.tag == "h4":
            t = _txt(el)
            if re.match(r"^\d{2} \w{3} \d{4}$", t):
                dia = t
                dias.append(t)
            continue
        if not dia:
            continue
        href = el.get("href") or ""
        t = _txt(el)
        if href.startswith("/articles/") and "New article published" in t:
            h3 = el.find(".//h3")
            titulo = _txt(h3) if h3 is not None else t
            artigos.append({"dia": dia, "chave": "art:" + href, "titulo": titulo, "url": BASE + href, "url_aviso": BASE + href})
        elif href.startswith("/models/") and "evaluation results available" in t:
            h3 = el.find(".//h3")
            nome = _txt(h3) if h3 is not None else ""
            m = re.search(r"Intelligence Index:\s*(\d+)", t)
            idx = m.group(1) if m else None
            slug = href.split("/")[2]
            base, var = nome, None
            mm = re.match(r"^(.*?)\s*\((.+)\)$", nome)
            if mm:
                base, var = mm.group(1), mm.group(2)
            k = (dia, slug)
            g = modelos.setdefault(k, {"dia": dia, "chave": f"mod:{slug}:{dia}", "nome": base,
                                         "url": BASE + "/models/" + slug, "vars": {}})
            g["vars"][var or ""] = idx
    itens = []
    # artigo do mesmo dia que fala de um modelo avaliado vira parte do item do modelo
    for a in artigos:
        alvo = None
        for g in modelos.values():
            if g["dia"] == a["dia"] and g["nome"].lower() in a["titulo"].lower():
                g.setdefault("artigos", []).append(a)
                alvo = g
                break
        if alvo is None:
            a["linha"] = f"{a['dia']} | Artigo: {a['titulo']} | {a['url']}"
            a["aviso_titulo"] = "Artigo novo no Artificial Analysis"
            a["aviso_corpo"] = a["titulo"]
            a["nome"] = a["titulo"]
            itens.append(a)
    for g in modelos.values():
        def ordem(v):
            p = v.split(",")[0].strip().lower()
            return (ORDEM.index(p) if p in ORDEM else 99, v)
        partes = []
        vs = list(g["vars"])
        suf = ""
        if len(vs) > 1 and all("," in v for v in vs):
            cauda = {v.split(",", 1)[1].strip() for v in vs}
            if len(cauda) == 1:
                suf = cauda.pop()
                g["vars"] = {v.split(",", 1)[0].strip(): i for v, i in g["vars"].items()}
        for v in sorted(g["vars"], key=ordem):
            i = g["vars"][v]
            rot = v or "nota"
            partes.append(f"{rot} {i}" if i else f"{rot} (sem nota)")
        nome = g["nome"] + (f" ({suf})" if suf else "")
        art = "".join(f" | Artigo: {a['titulo']}" for a in g.get("artigos", []))
        url = g["artigos"][0]["url"] if g.get("artigos") else g["url"]
        g["linha"] = f"{g['dia']} | Avaliado: {nome} | " + ", ".join(partes) + art + f" | {url}"
        g["nome"] = nome
        g["url_aviso"] = url
        g["aviso_titulo"] = f"{nome} avaliado"
        notas = ", ".join(partes) if any(g["vars"]) else ("Intelligence Index " + partes[0].split(" ", 1)[1])
        g["aviso_corpo"] = notas + "".join(f"\nArtigo: {a['titulo']}" for a in g.get("artigos", []))
        itens.append(g)
    itens.sort(key=lambda it: (dias.index(it["dia"]), 0 if it["chave"].startswith("mod") else 1))
    return itens


if __name__ == "__main__":
    import sys
    from scrapling.fetchers import Fetcher
    p = Fetcher.get(URL)
    for it in parse(p.html_content)[: int(sys.argv[1]) if len(sys.argv) > 1 else 15]:
        print(it["linha"])
