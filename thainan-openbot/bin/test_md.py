#!/usr/bin/env python3
"""
Testes unitários para o CLI md (thainan-openbot 0.64.0-r6):
1. Contagem de cores e tela vazia (imagem 1 cor e 2 cores -> saída 4 sem chamada de rede).
2. Imagem com conteúdo (> 2 cores) -> segue normalmente.
3. md ask com 9Router mockado -> resposta OK com modelo padrão e customizado (--modelo).
4. md ask com falha no 9Router -> fallback para o Moondream mockado com aviso explícito.
5. Garantia de não vazamento de segredos em stdout/stderr.
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MD_SCRIPT = os.path.join(HERE, "md")
PYTHON_BIN = sys.executable

# Se o python atual não tem PIL, tenta usar o venv local que tem
try:
    from PIL import Image
except ImportError:
    for candidate in [
        "/home/roberta/selah-papiro/.venv/bin/python3",
        "/home/roberta/trabalhos/hark-0511/.venv/bin/python3",
    ]:
        if os.path.isfile(candidate):
            PYTHON_BIN = candidate
            break


class MockNineRouterHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        dados = json.loads(body)
        self.server.requisicoes.append({
            "headers": dict(self.headers),
            "body": dados,
        })
        if getattr(self.server, "deve_falhar", False):
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "falha simulada no 9Router"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Descricao: Janela principal aberta no centro com dois botoes.",
                    }
                }
            ]
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))


class MockMoondreamHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        dados = json.loads(body)
        self.server.requisicoes.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": dados,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {"answer": "Moondream: tela com relogio"}
        self.wfile.write(json.dumps(resp).encode("utf-8"))


class TestMdCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_9r = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockNineRouterHandler)
        cls.server_9r.requisicoes = []
        cls.server_9r.deve_falhar = False
        cls.port_9r = cls.server_9r.server_port
        cls.th_9r = threading.Thread(target=cls.server_9r.serve_forever, daemon=True)
        cls.th_9r.start()

        cls.server_md = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockMoondreamHandler)
        cls.server_md.requisicoes = []
        cls.port_md = cls.server_md.server_port
        cls.th_md = threading.Thread(target=cls.server_md.serve_forever, daemon=True)
        cls.th_md.start()

        cls.tmpdir = tempfile.mkdtemp()

        # Cria imagens de teste via script python auxiliar usando PYTHON_BIN
        cria_imgs = f"""
from PIL import Image
im1 = Image.new("RGB", (64, 64), color=(0, 0, 0))
im1.save('{cls.tmpdir}/1cor.png')

im2 = Image.new("RGB", (64, 64), color=(0, 0, 0))
for x in range(32):
    for y in range(64):
        im2.putpixel((x, y), (255, 255, 255))
im2.save('{cls.tmpdir}/2cores.png')

im_ok = Image.new("RGB", (64, 64))
for y in range(64):
    for x in range(64):
        im_ok.putpixel((x, y), (x * 3, y * 3, 120))
im_ok.save('{cls.tmpdir}/conteudo.png')
"""
        subprocess.run([PYTHON_BIN, "-c", cria_imgs], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.server_9r.shutdown()
        cls.server_9r.server_close()
        cls.server_md.shutdown()
        cls.server_md.server_close()
        subprocess.run(["rm", "-rf", cls.tmpdir])

    def setUp(self):
        self.server_9r.requisicoes.clear()
        self.server_9r.deve_falhar = False
        self.server_md.requisicoes.clear()

    def rodar(self, args, env_extra=None):
        env = dict(os.environ)
        env["CONSELHO_URL"] = f"http://127.0.0.1:{self.port_9r}/v1/chat/completions"
        env["OPENBOT_9ROUTER_KEY"] = "segredo-mock-9router"
        env["MOONDREAM_API_KEY"] = "segredo-mock-moondream"
        # Ajusta para apontar base do Moondream se houver fallback
        # Injetamos via hack leve de urllib se necessário, ou mock direto
        if env_extra:
            env.update(env_extra)
        cmd = [PYTHON_BIN, MD_SCRIPT] + args
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_tela_vazia_uma_cor_sai_com_codigo_4_em_todos_subcomandos(self):
        p = f"{self.tmpdir}/1cor.png"
        for sub, args in [
            ("point", ["point", "alvo", "--image", p]),
            ("detect", ["detect", "alvo", "--image", p]),
            ("ask", ["ask", "pergunta", "--image", p]),
        ]:
            r = self.rodar(args)
            self.assertEqual(r.returncode, 4, f"{sub} deveria sair com 4, deu {r.returncode}: {r.stderr}")
            self.assertIn("tela vazia: display", r.stderr)
            self.assertIn("sem janela visivel", r.stderr)
            # Nenhuma chamada de rede deve ter sido feita
            self.assertEqual(len(self.server_9r.requisicoes), 0)
            self.assertEqual(len(self.server_md.requisicoes), 0)

    def test_tela_vazia_duas_cores_sai_com_codigo_4(self):
        p = f"{self.tmpdir}/2cores.png"
        r = self.rodar(["ask", "Descreva", "--image", p])
        self.assertEqual(r.returncode, 4)
        self.assertIn("tela vazia: display", r.stderr)
        self.assertEqual(len(self.server_9r.requisicoes), 0)

    def test_ask_chama_9router_com_conteudo_e_retorna_json(self):
        p = f"{self.tmpdir}/conteudo.png"
        r = self.rodar(["ask", "Descreva a tela", "--image", p])
        self.assertEqual(r.returncode, 0, r.stderr)
        dados = json.loads(r.stdout)
        self.assertEqual(dados["question"], "Descreva a tela")
        self.assertIn("Janela principal", dados["answer"])
        self.assertEqual(dados["provedor"], "9router")
        self.assertEqual(dados["modelo"], "ag/gemini-3.8-flash-high")

        # Verifica chamada de rede
        self.assertEqual(len(self.server_9r.requisicoes), 1)
        req = self.server_9r.requisicoes[0]
        self.assertEqual(req["headers"].get("Authorization"), "Bearer segredo-mock-9router")
        self.assertEqual(req["body"]["model"], "ag/gemini-3.8-flash-high")
        conteudo_user = req["body"]["messages"][0]["content"]
        self.assertEqual(conteudo_user[0]["type"], "text")
        self.assertIn("Descreva de forma objetiva", conteudo_user[0]["text"])
        self.assertEqual(conteudo_user[1]["type"], "image_url")
        self.assertTrue(conteudo_user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_ask_flag_modelo_customizado(self):
        p = f"{self.tmpdir}/conteudo.png"
        r = self.rodar(["ask", "Pergunta", "--image", p, "--modelo", "custom/modelo-v1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        dados = json.loads(r.stdout)
        self.assertEqual(dados["modelo"], "custom/modelo-v1")
        self.assertEqual(self.server_9r.requisicoes[0]["body"]["model"], "custom/modelo-v1")

    def test_ask_fallback_moondream_quando_9router_falha(self):
        p = f"{self.tmpdir}/conteudo.png"
        self.server_9r.deve_falhar = True

        # Para que o fallback caia no mock do moondream, criamos cópia apontando base do moondream
        with open(MD_SCRIPT, "r") as f:
            cod = f.read().replace('MOONDREAM_API_BASE = "https://api.moondream.ai/v1"', f'MOONDREAM_API_BASE = "http://127.0.0.1:{self.port_md}"')
        mock_script = f"{self.tmpdir}/md_mock_md.py"
        with open(mock_script, "w") as f:
            f.write(cod)

        env = dict(os.environ)
        env["CONSELHO_URL"] = f"http://127.0.0.1:{self.port_9r}/v1/chat/completions"
        env["OPENBOT_9ROUTER_KEY"] = "segredo-mock-9router"
        env["MOONDREAM_API_KEY"] = "segredo-mock-moondream"
        r = subprocess.run([PYTHON_BIN, mock_script, "ask", "Pergunta", "--image", p], capture_output=True, text=True, env=env)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(resposta do Moondream, menos confiável)", r.stderr)
        dados = json.loads(r.stdout)
        self.assertEqual(dados["provedor"], "moondream")
        self.assertIn("Moondream: tela com relogio", dados["answer"])

    def test_segredos_nunca_vazam(self):
        p = f"{self.tmpdir}/conteudo.png"
        r = self.rodar(["ask", "Pergunta", "--image", p])
        self.assertNotIn("segredo-mock-9router", r.stdout)
        self.assertNotIn("segredo-mock-9router", r.stderr)
        self.assertNotIn("segredo-mock-moondream", r.stdout)
        self.assertNotIn("segredo-mock-moondream", r.stderr)


if __name__ == "__main__":
    unittest.main()
EOF
