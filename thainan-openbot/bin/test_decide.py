#!/usr/bin/env python3
"""
Testes unitarios para a CLI decide do OpenBot.
Valida:
- Sucesso com Liquid AI (exit 0)
- Baixa confianca com saida ESCALAR (exit 3)
- Fallback para Jev via 9Router (quando Liquid falhar)
- Chave ausente (exit 1)
- Saida estruturada em JSON (--json)
- Envio de imagem no payload (--imagem)
- Garantia de nunca imprimir chaves de API
"""

import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

DECIDE_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "decide"))


class MockSystemOneHandler(http.server.BaseHTTPRequestHandler):
    liquid_response = None
    liquid_status = 200
    liquid_delay = 0

    jev_response = None
    jev_status = 200

    requests_log = []

    def log_message(self, format_str, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            body_json = json.loads(raw_body)
        except Exception:
            body_json = None

        req_info = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": body_json,
        }
        MockSystemOneHandler.requests_log.append(req_info)

        if self.path.startswith("/liquid"):
            if MockSystemOneHandler.liquid_status != 200:
                self.send_response(MockSystemOneHandler.liquid_status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "liquid failure"}')
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = MockSystemOneHandler.liquid_response or {}
            self.wfile.write(json.dumps(resp).encode("utf-8"))

        elif self.path.startswith("/jev") or self.path.startswith("/v1/systemone"):
            if MockSystemOneHandler.jev_status != 200:
                self.send_response(MockSystemOneHandler.jev_status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "jev failure"}')
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = MockSystemOneHandler.jev_response or {}
            self.wfile.write(json.dumps(resp).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()


class TestDecideCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockSystemOneHandler)
        cls.port = cls.httpd.server_address[1]
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

        cls.liquid_url = f"http://127.0.0.1:{cls.port}/liquid"
        cls.jev_url = f"http://127.0.0.1:{cls.port}/jev"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        MockSystemOneHandler.requests_log.clear()
        MockSystemOneHandler.liquid_response = None
        MockSystemOneHandler.liquid_status = 200
        MockSystemOneHandler.liquid_delay = 0
        MockSystemOneHandler.jev_response = None
        MockSystemOneHandler.jev_status = 200

    def run_decide(self, args, env=None):
        full_env = os.environ.copy()
        # Limpa chaves do ambiente a menos que especificadas no teste
        for k in ["LIQUID_API_KEY", "NINEROUTER_API_KEY", "OPENBOT_9ROUTER_KEY", "APP_DATA_DIR"]:
            full_env.pop(k, None)
        if env:
            full_env.update(env)

        cmd = [sys.executable, DECIDE_SCRIPT] + args
        proc = subprocess.run(
            cmd,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc

    def test_sucesso_liquid(self):
        MockSystemOneHandler.liquid_response = {
            "model": "d1-ultra",
            "answers": {
                "decisao": {
                    "type": "choice",
                    "choice": "opt_submit",
                    "confidence": 0.89,
                    "probabilities": {
                        "opt_submit": 0.89,
                        "opt_cancel": 0.11,
                    },
                }
            },
            "usage": {
                "input_tokens": 128,
                "cost": 0.00018,
            },
        }

        mock_auth_token = "example_mock_token_9999"
        args = [
            "--objetivo", "Enviar o formulario de cadastro",
            "--estado", "Janela de cadastro com botoes [id=opt_submit, Enviar] e [id=opt_cancel, Cancelar]",
            "--opcao", "opt_submit=Clicar no botao Enviar",
            "--opcao", "opt_cancel=Cancelar a operacao",
            "--liquid-url", self.liquid_url,
            "--ninerouter-url", self.jev_url,
        ]
        proc = self.run_decide(args, env={"LIQUID_API_KEY": mock_auth_token})

        self.assertEqual(proc.returncode, 0)
        self.assertIn("opcao: opt_submit", proc.stdout)
        self.assertIn("confianca: 0.8900", proc.stdout)
        self.assertIn("custo: 0.00018", proc.stdout)
        self.assertNotIn("ESCALAR", proc.stdout)
        self.assertNotIn(mock_auth_token, proc.stdout)
        self.assertNotIn(mock_auth_token, proc.stderr)

        self.assertEqual(len(MockSystemOneHandler.requests_log), 1)
        req = MockSystemOneHandler.requests_log[0]
        self.assertEqual(req["headers"].get("Authorization"), f"Bearer {mock_auth_token}")
        self.assertEqual(req["body"].get("model"), "d1-ultra")
        self.assertIn("state", req["body"])
        self.assertIn("context", req["body"])
        self.assertEqual(req["body"]["state"], "Janela de cadastro com botoes [id=opt_submit, Enviar] e [id=opt_cancel, Cancelar]")
        criteria = req["body"]["questions"]["decisao"]["criteria"]
        self.assertIsInstance(criteria, dict)
        self.assertIn("opt_submit", criteria)
        self.assertNotIn("images", req["body"])

    def test_baixa_confianca_exit_3_escalar(self):
        MockSystemOneHandler.liquid_response = {
            "model": "d1-ultra",
            "answers": {
                "decisao": {
                    "type": "choice",
                    "choice": "opt_a",
                    "confidence": 0.22,
                    "probabilities": {
                        "opt_a": 0.22,
                        "opt_b": 0.21,
                        "opt_c": 0.20,
                    },
                }
            },
            "usage": {
                "input_tokens": 95,
                "cost": 0.0001,
            },
        }

        args = [
            "--objetivo", "Decidir proximo passo ambiguo",
            "--estado", "Interface sem indicacao clara de foco",
            "--opcao", "opt_a=Opcao A",
            "--opcao", "opt_b=Opcao B",
            "--opcao", "opt_c=Opcao C",
            "--limiar", "0.35",
            "--liquid-url", self.liquid_url,
            "--ninerouter-url", self.jev_url,
        ]
        proc = self.run_decide(args, env={"LIQUID_API_KEY": "dummy_liq_key"})

        self.assertEqual(proc.returncode, 3)
        self.assertIn("ESCALAR", proc.stdout)
        self.assertIn("opcao: opt_a", proc.stdout)
        self.assertIn("confianca: 0.2200", proc.stdout)

    def test_fallback_jev_quando_liquid_falha(self):
        # Liquid retorna erro 500
        MockSystemOneHandler.liquid_status = 500

        # Jev (9Router) responde com sucesso
        MockSystemOneHandler.jev_response = {
            "model": "~typesafe/jev-latest",
            "answers": {
                "decisao": {
                    "type": "choice",
                    "choice": "opt_cancel",
                    "confidence": 0.92,
                    "probabilities": {
                        "opt_submit": 0.08,
                        "opt_cancel": 0.92,
                    },
                }
            },
            "usage": {
                "input_tokens": 110,
                "cost": 0.0,
            },
        }

        mock_liquid_token = "example_liquid_token_fail"
        mock_jev_token = "example_jev_token_backup"
        args = [
            "--objetivo", "Cancelar processo",
            "--estado", "Tela de confirmacao",
            "--opcao", "opt_submit=Confirmar",
            "--opcao", "opt_cancel=Voltar",
            "--liquid-url", self.liquid_url,
            "--ninerouter-url", self.jev_url,
        ]
        proc = self.run_decide(args, env={
            "LIQUID_API_KEY": mock_liquid_token,
            "NINEROUTER_API_KEY": mock_jev_token,
        })

        self.assertEqual(proc.returncode, 0)
        self.assertIn("opcao: opt_cancel", proc.stdout)
        self.assertIn("confianca: 0.9200", proc.stdout)
        self.assertNotIn(mock_liquid_token, proc.stdout)
        self.assertNotIn(mock_jev_token, proc.stdout)

        # Verifica se tentou Liquid primeiro e depois Jev
        self.assertEqual(len(MockSystemOneHandler.requests_log), 2)
        req_liquid = MockSystemOneHandler.requests_log[0]
        req_jev = MockSystemOneHandler.requests_log[1]
        self.assertEqual(req_liquid["path"], "/liquid")
        self.assertEqual(req_jev["path"], "/jev")
        self.assertEqual(req_jev["body"].get("model"), "~typesafe/jev-latest")
        self.assertIn("state", req_jev["body"])
        self.assertIn("context", req_jev["body"])
        self.assertIsInstance(req_jev["body"]["questions"]["decisao"]["criteria"], dict)
        self.assertNotIn("images", req_jev["body"])
        self.assertEqual(req_jev["headers"].get("Authorization"), f"Bearer {mock_jev_token}")

    def test_chave_ausente_exit_1(self):
        args = [
            "--objetivo", "Qualquer",
            "--estado", "Qualquer",
            "--opcao", "op1=Descricao 1",
            "--opcao", "op2=Descricao 2",
            "--liquid-url", self.liquid_url,
            "--ninerouter-url", self.jev_url,
        ]
        proc = self.run_decide(args, env={})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("erro: chave ausente", proc.stderr)

    def test_saida_json_sucesso_e_escalar(self):
        MockSystemOneHandler.liquid_response = {
            "model": "d1-ultra",
            "answers": {
                "decisao": {
                    "type": "choice",
                    "choice": "btn_ok",
                    "confidence": 0.95,
                    "probabilities": {"btn_ok": 0.95, "btn_no": 0.05},
                }
            },
            "usage": {"input_tokens": 80, "cost": 0.0001},
        }

        # 1. Sucesso em JSON
        args_ok = [
            "--objetivo", "Confirmar",
            "--estado", "Dialogo",
            "--opcao", "btn_ok=OK",
            "--opcao", "btn_no=Nao",
            "--json",
            "--liquid-url", self.liquid_url,
            "--ninerouter-url", self.jev_url,
        ]
        proc_ok = self.run_decide(args_ok, env={"LIQUID_API_KEY": "valid_key"})
        self.assertEqual(proc_ok.returncode, 0)
        data_ok = json.loads(proc_ok.stdout)
        self.assertEqual(data_ok["opcao"], "btn_ok")
        self.assertEqual(data_ok["confianca"], 0.95)
        self.assertNotIn("ESCALAR", proc_ok.stdout)

        # 2. Baixa confianca em JSON (exit 3 e status ESCALAR)
        MockSystemOneHandler.liquid_response["answers"]["decisao"]["confidence"] = 0.20
        args_low = args_ok + ["--limiar", "0.35"]
        proc_low = self.run_decide(args_low, env={"LIQUID_API_KEY": "valid_key"})
        self.assertEqual(proc_low.returncode, 3)
        data_low = json.loads(proc_low.stdout)
        self.assertEqual(data_low["status"], "ESCALAR")
        self.assertTrue(data_low["escalar"])
        self.assertEqual(data_low["confianca"], 0.20)

    def test_envio_de_imagem(self):
        MockSystemOneHandler.liquid_response = {
            "model": "d1-ultra",
            "answers": {
                "decisao": {
                    "type": "choice",
                    "choice": "opt_img",
                    "confidence": 0.90,
                    "probabilities": {"opt_img": 0.90},
                }
            },
            "usage": {"input_tokens": 250, "cost": 0.0003},
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
            img_path = tf.name

        try:
            args = [
                "--objetivo", "Identificar alvo na tela",
                "--estado", "Captura grafica",
                "--opcao", "opt_img=Item grafico",
                "--imagem", img_path,
                "--liquid-url", self.liquid_url,
                "--ninerouter-url", self.jev_url,
            ]
            proc = self.run_decide(args, env={"LIQUID_API_KEY": "key_with_img"})
            self.assertEqual(proc.returncode, 0)

            self.assertEqual(len(MockSystemOneHandler.requests_log), 1)
            req = MockSystemOneHandler.requests_log[0]
            self.assertIn("images", req["body"])
            self.assertEqual(len(req["body"]["images"]), 1)
            self.assertTrue(req["body"]["images"][0].startswith("data:image/"))
        finally:
            if os.path.exists(img_path):
                os.unlink(img_path)

    def test_chave_via_arquivo_config(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "config", "liquid.key")
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            with open(key_path, "w", encoding="utf-8") as f:
                f.write("key_from_file_7788")
            os.chmod(key_path, 0o600)

            MockSystemOneHandler.liquid_response = {
                "model": "d1-ultra",
                "answers": {
                    "decisao": {
                        "type": "choice",
                        "choice": "opt_file",
                        "confidence": 0.85,
                        "probabilities": {"opt_file": 0.85},
                    }
                },
                "usage": {"input_tokens": 90, "cost": 0.0001},
            }

            args = [
                "--objetivo", "Verificar chave em arquivo",
                "--estado", "Teste",
                "--opcao", "opt_file=Opcao do arquivo",
                "--liquid-url", self.liquid_url,
                "--ninerouter-url", self.jev_url,
            ]
            proc = self.run_decide(args, env={"APP_DATA_DIR": td})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("opcao: opt_file", proc.stdout)

            req = MockSystemOneHandler.requests_log[0]
            self.assertEqual(req["headers"].get("Authorization"), "Bearer key_from_file_7788")


if __name__ == "__main__":
    unittest.main()
