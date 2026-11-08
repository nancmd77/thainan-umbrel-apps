#!/usr/bin/env python3
"""
Testes unitarios para a CLI conselho do OpenBot.
Usa um servidor HTTP falso que imita /v1/chat/completions do 9Router.
Valida:
- Sucesso com 3 modelos e sintese do presidente (exit 0)
- Respostas anonimas: o presidente nao recebe nomes de modelos
- Falha 429 e timeout: segue com os que responderam e informa quais falharam
- Um so modelo respondendo: devolve essa resposta com aviso, sem chamar presidente
- Nenhum respondendo: exit 1
- Presidente falha: usa o proximo candidato
- Presidente fora do JSON: devolve texto bruto com confianca baixa
- Contexto por stdin e por arquivo
- --modelos sobrescreve CONSELHO_MODELOS
- Sem --modelos nem CONSELHO_MODELOS: consulta os 3 modelos padrao e o primeiro preside
- JSON estruturado (--json)
- Registro em CONSELHO_LOG sem segredos e com pergunta de no maximo 200 caracteres
- Chave ausente (exit 1) e chave nunca impressa
"""

import http.server
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

CONSELHO_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "conselho"))
CHAVE_TESTE = "sk-teste-segredo-9router-123"

PRESIDENTE_JSON = json.dumps({
    "recomendacao": "Contratar com ressalvas",
    "confianca": "média",
    "concordam": "Os modelos concordam que o risco e controlavel.",
    "divergem": "Um deles acha o prazo curto demais.",
}, ensure_ascii=False)


class FakeNineRouterHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeNineRouter/1.0"

    def log_message(self, format_str, *args):
        pass

    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _texto(self, texto):
        return {"choices": [{"message": {"role": "assistant", "content": texto}}]}

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length", "0"))
        corpo = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        modelo = corpo["model"]
        mensagens = corpo["messages"]
        presidente_role = "Voce preside" in mensagens[0]["content"]
        self.server.registrar({
            "modelo": modelo,
            "papel": "presidente" if presidente_role else "respondente",
            "authorization": self.headers.get("Authorization"),
            "mensagens": mensagens,
        })

        if presidente_role:
            if modelo == "fake/presidente-fail":
                return self._responder(500, {"error": "boom"})
            if modelo == "fake/presidente-quebrado":
                return self._responder(200, self._texto("Acho que sim, mas sem formato."))
            return self._responder(200, self._texto(PRESIDENTE_JSON))

        if modelo == "fake/rate-limit":
            return self._responder(429, {"error": "rate limit"})
        if modelo == "fake/err500":
            return self._responder(500, {"error": "boom"})
        if modelo == "fake/timeout":
            # Passa do timeout curto do cliente; falha de escrita e esperada.
            threading.Event().wait(2.0)
            try:
                return self._responder(200, self._texto("tarde demais"))
            except OSError:
                return None
        letra = modelo.rsplit("-", 1)[-1].upper()
        return self._responder(200, self._texto(f"Resposta {letra}: seguir em frente."))


class FakeNineRouterServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeNineRouterHandler)
        self._lock = threading.Lock()
        self.requisicoes = []

    def registrar(self, item):
        with self._lock:
            self.requisicoes.append(item)

    def limpar(self):
        with self._lock:
            self.requisicoes = []

    def da_fase(self, papel):
        with self._lock:
            return [r for r in self.requisicoes if r["papel"] == papel]


def carregar_conselho_como_modulo():
    loader = importlib.machinery.SourceFileLoader("conselho_cli", CONSELHO_SCRIPT)
    spec = importlib.util.spec_from_loader("conselho_cli", loader)
    modulo = importlib.util.module_from_spec(spec)
    loader.exec_module(modulo)
    return modulo


class TestConselhoCLI(unittest.TestCase):
    servidor = None
    thread = None

    @classmethod
    def setUpClass(cls):
        cls.servidor = FakeNineRouterServer()
        cls.thread = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def setUp(self):
        self.servidor.limpar()
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmp.name, "conselho.log")
        self.env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CONSELHO_") and k != "OPENBOT_9ROUTER_KEY"
        }
        self.env.update({
            "CONSELHO_URL": f"http://127.0.0.1:{self.servidor.server_address[1]}/v1/chat/completions",
            "CONSELHO_LOG": self.log_path,
            "CONSELHO_KEY_FILE": os.path.join(self.tmp.name, "ausente.key"),
            "CONSELHO_TIMEOUT": "0.5",
            "CONSELHO_PRESIDENTE": "fake/presidente",
            "OPENBOT_9ROUTER_KEY": CHAVE_TESTE,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def rodar(self, argumentos, entrada=None, env_extra=None):
        env = dict(self.env)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, CONSELHO_SCRIPT] + argumentos,
            input=entrada,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def registro(self):
        with open(self.log_path, "r", encoding="utf-8") as f:
            return [json.loads(linha) for linha in f if linha.strip()]

    def test_sucesso_com_tres_modelos_e_sintese_do_presidente(self):
        r = self.rodar(["Trocar o fornecedor?", "--modelos", "fake/ok-a,fake/ok-b,fake/ok-c"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Recomendação: Contratar com ressalvas", r.stdout)
        self.assertIn("Confiança: média", r.stdout)
        self.assertIn("Concordam: Os modelos concordam que o risco e controlavel.", r.stdout)
        self.assertIn("Divergem: Um deles acha o prazo curto demais.", r.stdout)
        self.assertIn("Modelos usados: fake/ok-a, fake/ok-b, fake/ok-c", r.stdout)
        self.assertIn("Falhas: nenhuma", r.stdout)

    def test_presidente_recebe_respostas_anonimas(self):
        self.rodar(["Trocar o fornecedor?", "--modelos", "fake/ok-a,fake/ok-b,fake/ok-c"])
        presidencia = self.servidor.da_fase("presidente")
        self.assertEqual(len(presidencia), 1)
        prompt = presidencia[0]["mensagens"][1]["content"]
        self.assertIn("--- Resposta A ---", prompt)
        self.assertIn("--- Resposta B ---", prompt)
        self.assertIn("--- Resposta C ---", prompt)
        self.assertNotIn("fake/ok-", prompt)

    def test_falha_429_e_timeout_segue_com_os_que_responderam(self):
        r = self.rodar(["Apagar a tabela antiga?", "--modelos", "fake/ok-a,fake/ok-b,fake/rate-limit,fake/timeout"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Modelos usados: fake/ok-a, fake/ok-b", r.stdout)
        self.assertIn("fake/rate-limit (429 limite de uso)", r.stdout)
        self.assertIn("fake/timeout (timeout)", r.stdout)
        prompt = self.servidor.da_fase("presidente")[0]["mensagens"][1]["content"]
        self.assertIn("--- Resposta B ---", prompt)
        self.assertNotIn("--- Resposta C ---", prompt)

    def test_um_so_respondendo_devolve_com_aviso_sem_presidente(self):
        r = self.rodar(["Mexer em producao?", "--modelos", "fake/ok-a,fake/rate-limit,fake/err500"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Recomendação: Resposta A: seguir em frente.", r.stdout)
        self.assertIn("Confiança: baixa", r.stdout)
        self.assertIn("Aviso: so 1 modelo respondeu", r.stdout)
        self.assertEqual(self.servidor.da_fase("presidente"), [])

    def test_nenhum_respondendo_sai_com_erro(self):
        r = self.rodar(["Mexer em producao?", "--modelos", "fake/rate-limit,fake/err500"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("nenhum modelo respondeu", r.stderr)
        self.assertIn("fake/err500 (HTTP 500)", r.stderr)
        registro = self.registro()
        self.assertEqual(registro[-1]["modelos"], [])

    def test_presidente_falha_usa_proximo_candidato(self):
        r = self.rodar(
            ["Escolher biblioteca?", "--modelos", "fake/ok-a,fake/ok-b"],
            env_extra={"CONSELHO_PRESIDENTE": "fake/presidente-fail"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Recomendação: Contratar com ressalvas", r.stdout)
        self.assertIn("fake/presidente-fail (presidente) (HTTP 500)", r.stdout)
        papeis = [p["modelo"] for p in self.servidor.da_fase("presidente")]
        self.assertEqual(papeis, ["fake/presidente-fail", "fake/ok-a"])

    def test_presidente_fora_do_formato_devolve_texto_bruto(self):
        r = self.rodar(
            ["Escolher biblioteca?", "--modelos", "fake/ok-a,fake/ok-b"],
            env_extra={"CONSELHO_PRESIDENTE": "fake/presidente-quebrado"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Recomendação: Acho que sim, mas sem formato.", r.stdout)
        self.assertIn("Confiança: baixa", r.stdout)
        self.assertIn("fora do formato JSON", r.stdout)

    def test_contexto_por_stdin(self):
        r = self.rodar(
            ["Pergunta X?", "--modelos", "fake/ok-a,fake/ok-b", "--contexto", "-"],
            entrada="CONTEXTO_MARCADOR_STDIN",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for req in self.servidor.da_fase("respondente"):
            self.assertIn("CONTEXTO_MARCADOR_STDIN", req["mensagens"][1]["content"])

    def test_contexto_por_arquivo(self):
        caminho = os.path.join(self.tmp.name, "contexto.md")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("CONTEXTO_MARCADOR_ARQUIVO")
        r = self.rodar(["Pergunta X?", "--modelos", "fake/ok-a,fake/ok-b", "--contexto", caminho])
        self.assertEqual(r.returncode, 0, r.stderr)
        for req in self.servidor.da_fase("respondente"):
            self.assertIn("CONTEXTO_MARCADOR_ARQUIVO", req["mensagens"][1]["content"])

    def test_modelos_da_flag_sobrescreve_env(self):
        r = self.rodar(
            ["Pergunta X?", "--modelos", "fake/ok-a,fake/ok-b"],
            env_extra={"CONSELHO_MODELOS": "fake/ok-c,fake/ok-a"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Modelos usados: fake/ok-a, fake/ok-b", r.stdout)

    def test_modelos_da_env_quando_sem_flag(self):
        r = self.rodar(["Pergunta X?"], env_extra={"CONSELHO_MODELOS": "fake/ok-b,fake/ok-c"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Modelos usados: fake/ok-b, fake/ok-c", r.stdout)

    def test_sem_flag_nem_env_usa_trio_padrao_e_o_primeiro_preside(self):
        padrao = carregar_conselho_como_modulo().MODELOS_PADRAO.split(",")
        env = dict(self.env)
        env.pop("CONSELHO_PRESIDENTE")
        r = subprocess.run(
            [sys.executable, CONSELHO_SCRIPT, "Pergunta X?"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(padrao), 3)
        self.assertCountEqual([q["modelo"] for q in self.servidor.da_fase("respondente")], padrao)
        self.assertEqual([q["modelo"] for q in self.servidor.da_fase("presidente")], padrao[:1])

    def test_saida_json_estruturada(self):
        r = self.rodar(
            ["Apagar dados?", "--modelos", "fake/ok-a,fake/ok-b,fake/rate-limit", "--json"]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        dados = json.loads(r.stdout)
        self.assertEqual(dados["recomendacao"], "Contratar com ressalvas")
        self.assertEqual(dados["confianca"], "média")
        self.assertEqual(dados["modelos_usados"], ["fake/ok-a", "fake/ok-b"])
        self.assertEqual(dados["presidente"], "fake/presidente")
        self.assertEqual(dados["falhas"], [{"modelo": "fake/rate-limit", "motivo": "429 limite de uso"}])
        self.assertIsNone(dados["aviso"])
        self.assertEqual(set(dados["respostas"]), {"fake/ok-a", "fake/ok-b"})

    def test_registro_em_log_sem_segredo_e_pergunta_limitada(self):
        pergunta = "P" * 300
        r = self.rodar(
            [pergunta, "--modelos", "fake/ok-a,fake/rate-limit"],
            env_extra={"OPENBOT_9ROUTER_KEY": CHAVE_TESTE},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        registros = self.registro()
        self.assertEqual(len(registros), 1)
        entrada = registros[0]
        self.assertEqual(set(entrada), {"data", "pergunta", "modelos", "falhas", "presidente", "confianca"})
        self.assertLessEqual(len(entrada["pergunta"]), 200)
        self.assertEqual(entrada["modelos"], ["fake/ok-a"])
        self.assertEqual(entrada["falhas"], ["fake/rate-limit"])
        self.assertEqual(entrada["confianca"], "baixa")
        with open(self.log_path, "r", encoding="utf-8") as f:
            self.assertNotIn(CHAVE_TESTE, f.read())

    def test_chave_so_vai_no_header_e_nunca_na_saida(self):
        r = self.rodar(["Pergunta X?", "--modelos", "fake/ok-a,fake/ok-b"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(CHAVE_TESTE, r.stdout)
        self.assertNotIn(CHAVE_TESTE, r.stderr)
        for req in self.servidor.requisicoes:
            self.assertEqual(req["authorization"], f"Bearer {CHAVE_TESTE}")

    def test_chave_ausente_sai_com_erro(self):
        env = dict(self.env)
        env.pop("OPENBOT_9ROUTER_KEY", None)
        r = subprocess.run(
            [sys.executable, CONSELHO_SCRIPT, "Pergunta X?", "--modelos", "fake/ok-a,fake/ok-b"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("chave 9Router ausente", r.stderr)
        self.assertEqual(self.servidor.requisicoes, [])

    def test_normalizar_confianca(self):
        modulo = carregar_conselho_como_modulo()
        self.assertEqual(modulo.normalizar_confianca("ALTA"), "alta")
        self.assertEqual(modulo.normalizar_confianca("media"), "média")
        self.assertEqual(modulo.normalizar_confianca("média"), "média")
        self.assertEqual(modulo.normalizar_confianca("talvez"), "baixa")
        self.assertEqual(modulo.normalizar_confianca(None), "baixa")

    def test_interpretar_sintese_com_cercas_de_markdown(self):
        modulo = carregar_conselho_como_modulo()
        bruto = "```json\n" + PRESIDENTE_JSON + "\n```"
        resultado = modulo.interpretar_sintese(bruto)
        self.assertEqual(resultado["recomendacao"], "Contratar com ressalvas")
        self.assertEqual(resultado["confianca"], "média")
        self.assertIsNone(resultado["aviso"])


if __name__ == "__main__":
    unittest.main()
