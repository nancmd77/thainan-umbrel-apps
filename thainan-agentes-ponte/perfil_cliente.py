"""perfil_cliente: descobre qual cliente chamou a ponte e o que ele sabe mostrar.

ESBOÇO, AINDA NÃO LIGADO na ponte (server.py 1.1.0 não importa este arquivo e o app só baixa
server.py). Lê as entradas do registro de clientes (/data/registro/clientes.jsonl, uma linha JSON
por requisição) e devolve um perfil. Uso hoje, só para conferir o registro:

    python3 perfil_cliente.py clientes.jsonl          # resumo por cliente detectado
    python3 perfil_cliente.py clientes.jsonl --linhas # perfil de cada linha

Clientes: nod, hermes, openbot, desconhecido. Modelo único `agentes-nonve` para todos; o perfil
só muda a FORMA da resposta (e, mais tarde, se a ponte deixa o cliente usar as próprias ferramentas).

Capacidades:
- formato: "markdown_completo" (títulos, tabelas, listas, código, imagens em markdown),
  "markdown_simples" (negrito, listas, links, código curto; sem tabela nem títulos) ou "texto".
- imagem: "inline" (pode mandar ![](url) ou data URL), "link" (só a URL) ou "nada".
- ferramentas_proprias: True quando o cliente manda `tools` e saberia executar tool_calls
  devolvidos pela ponte (hoje a ponte descarta `tools` para todos).

Fallback (cliente desconhecido ou sinal fraco): markdown_simples, imagem como link, sem ferramentas.

Sinais (o que já está confirmado e o que é hipótese a conferir no registro real):
- nod: o último turno do usuário traz o embrulho JSON {"mode": ..., "currentInstruction": ...}
  (CONFIRMADO no código do Nod e nos testes de 19/11; campo corpo.embrulho_nod.presente). Manda
  `tools` sempre (confirmado). User-Agent: a conferir (o provedor openai-completions do Nod usa
  SDK JS; se for o SDK da OpenAI, vem "OpenAI/JS x" com X-Stainless-Lang: js).
- hermes: Hermes Agent com provedor custom usa o SDK Python da OpenAI, então manda
  "User-Agent: OpenAI/Python x" e X-Stainless-* (fonte: github.com/NousResearch/hermes-agent
  issue 40033 e PR 145799, captura de cabeçalhos). Pode trocar com model.default_headers no
  config.yaml (cli-config.yaml.example). Prompt de sistema costuma citar "Hermes" (hipótese).
- openbot: app thainan-openbot do Nonve; cabeçalhos e prompt AINDA DESCONHECIDOS (nenhuma
  chamada dele registrada). Hipótese: prompt de sistema cita "OpenBot".
- Sinal forte para todos: cabeçalho explícito X-Cliente (ou X-Client-Name / X-Title) posto na
  configuração de cada cliente. Quando existe, vale mais que qualquer heurística.

As capacidades de cada cliente abaixo são o PONTO DE PARTIDA, a conferir antes de ativar
(como cada interface desenha markdown e imagem).
"""
import json
import sys
from collections import Counter

CAPACIDADES = {
    "nod": {"formato": "markdown_completo", "imagem": "inline", "ferramentas_proprias": True},
    "hermes": {"formato": "markdown_simples", "imagem": "link", "ferramentas_proprias": True},
    "openbot": {"formato": "markdown_completo", "imagem": "inline", "ferramentas_proprias": True},
    "desconhecido": {"formato": "markdown_simples", "imagem": "link", "ferramentas_proprias": False},
}
FALLBACK = "desconhecido"
CABECALHOS_EXPLICITOS = ("x-cliente", "x-client-name", "x-title")


def _cab(entrada):
    return {k.lower(): str(v) for k, v in (entrada.get("cabecalhos") or {}).items()}


def detectar(entrada):
    """Devolve (cliente, confianca 0..1, motivos)."""
    cab = _cab(entrada)
    corpo = entrada.get("corpo") or {}
    ua = (entrada.get("user_agent") or cab.get("user-agent") or "").lower()
    sistema = (corpo.get("sistema_inicio") or "").lower()
    motivos = []

    for h in CABECALHOS_EXPLICITOS:
        v = cab.get(h, "").lower()
        for nome in ("nod", "hermes", "openbot"):
            if nome in v:
                return nome, 1.0, ["cabeçalho %s=%s" % (h, v)]

    pontos = Counter()
    if (corpo.get("embrulho_nod") or {}).get("presente"):
        pontos["nod"] += 0.8
        motivos.append("embrulho do Nod no último turno")
    if "hermes" in sistema or "hermes" in ua:
        pontos["hermes"] += 0.6
        motivos.append("'hermes' no prompt ou no user-agent")
    if "openbot" in sistema or "openbot" in ua:
        pontos["openbot"] += 0.6
        motivos.append("'openbot' no prompt ou no user-agent")
    if "you are nod" in sistema or "você é o nod" in sistema:
        pontos["nod"] += 0.3
        motivos.append("prompt de sistema do Nod")
    lang = cab.get("x-stainless-lang", "")
    if ua.startswith("openai/python") or lang == "python":
        pontos["hermes"] += 0.2  # fraco: qualquer cliente Python com o SDK da OpenAI
        motivos.append("SDK Python da OpenAI (sinal fraco)")

    if not pontos:
        return FALLBACK, 0.0, ["nenhum sinal"]
    cliente, conf = pontos.most_common(1)[0]
    if conf < 0.5:
        return FALLBACK, conf, motivos + ["sinal fraco, ficou no fallback"]
    return cliente, min(conf, 1.0), motivos


def perfil(entrada):
    cliente, conf, motivos = detectar(entrada)
    cap = dict(CAPACIDADES.get(cliente, CAPACIDADES[FALLBACK]))
    corpo = entrada.get("corpo") or {}
    # Sem `tools` no pedido o cliente não tem como executar tool_calls, seja quem for.
    if not (corpo.get("tools") or {}).get("presente"):
        cap["ferramentas_proprias"] = False
    return {"cliente": cliente, "confianca": round(conf, 2), "motivos": motivos, **cap}


def instrucao_de_formato(p):
    """Texto que a ponte poria antes do pedido à Frente quando a detecção for ligada."""
    f = {"markdown_completo": "Pode usar markdown completo (títulos, listas, tabelas, código).",
         "markdown_simples": "Use markdown simples: parágrafos curtos, negrito e listas; sem tabelas nem títulos.",
         "texto": "Responda em texto puro, sem nenhum markdown."}[p["formato"]]
    i = {"inline": "Imagens podem ir em markdown ![descrição](url).",
         "link": "Para imagens, mande só o link.",
         "nada": "Não mande imagens nem links de imagem."}[p["imagem"]]
    return f + " " + i


def _ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        for l in f:
            try:
                yield json.loads(l)
            except Exception:
                continue


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    linhas = [e for e in _ler(sys.argv[1]) if e.get("rota", "").endswith("chat/completions")]
    if "--linhas" in sys.argv:
        for e in linhas:
            print(e.get("ts"), e.get("user_agent"), json.dumps(perfil(e), ensure_ascii=False))
    else:
        c = Counter((perfil(e)["cliente"], e.get("user_agent")) for e in linhas)
        for (cli, ua), n in c.most_common():
            print("%-13s %4d  %s" % (cli, n, ua))
