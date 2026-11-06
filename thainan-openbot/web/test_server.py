#!/usr/bin/env python3
import base64
import http.server
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

from server import (
    DEFAULT_EMAIL,
    OpenBotWebHandler,
    ansi_to_html,
    query_control_socket,
)
import server


class MockOpenBotUnixHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/status":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if getattr(self.server, "logged_in", False):
                self.wfile.write(b"version=0.62.0\naccount=signed_in\nemail=thainancaldeira75@gmail.com\nserver=online\nserver_name=OpenBot\n")
            else:
                self.wfile.write(b"version=0.62.0\naccount=signed_out\nserver=offline\nserver_name=OpenBot\n")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        params = urllib.parse.parse_qs(raw)

        if self.path == "/v1/login/start":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"challenge=ch_test_1234\nexpires_at=1730000000000\n")
        elif self.path == "/v1/login/verify":
            code = params.get("code", [""])[0]
            if code == "123456":
                self.server.logged_in = True
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"version=0.62.0\naccount=signed_in\nemail=thainancaldeira75@gmail.com\nserver=online\nserver_name=OpenBot\n")
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"error=invalid_code\nmessage=Codigo invalido\n")
        elif self.path == "/v1/mobile-connect":
            sample_ansi = "\x1b[40m\x1b[37m▀▄ \x1b[0m"
            b64_qr = base64.b64encode(sample_ansi.encode("utf-8")).decode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"qr={b64_qr}\nexpires_at=1730000120000\n".encode("utf-8"))
        elif self.path == "/v1/logout":
            self.server.logged_in = False
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"version=0.62.0\naccount=signed_out\nserver=offline\nserver_name=OpenBot\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass

class MockExternalAPIHandler(http.server.BaseHTTPRequestHandler):
    liquid_response = None
    liquid_status = 200
    typesafe_response = None
    typesafe_status = 200
    moondream_response = None
    moondream_status = 200
    requests_log = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = raw

        MockExternalAPIHandler.requests_log.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })

        if self.path.startswith("/liquid"):
            self.send_response(MockExternalAPIHandler.liquid_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = MockExternalAPIHandler.liquid_response or {}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path.startswith("/typesafe"):
            self.send_response(MockExternalAPIHandler.typesafe_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = MockExternalAPIHandler.typesafe_response or {}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path.startswith("/moondream"):
            self.send_response(MockExternalAPIHandler.moondream_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = MockExternalAPIHandler.moondream_response or {}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class UnixServer(http.server.HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.sock_path = os.path.join(cls.temp_dir.name, "control.sock")
        server.SOCKET_PATH = cls.sock_path

        cls.mock_openbot = UnixServer(cls.sock_path, MockOpenBotUnixHandler)
        cls.mock_openbot.logged_in = False
        cls.t_openbot = threading.Thread(target=cls.mock_openbot.serve_forever, daemon=True)
        cls.t_openbot.start()

        # Start web server on ephemeral port
        cls.web_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OpenBotWebHandler)
        cls.web_port = cls.web_server.server_address[1]
        cls.t_web = threading.Thread(target=cls.web_server.serve_forever, daemon=True)
        cls.t_web.start()
        cls.base_url = f"http://127.0.0.1:{cls.web_port}"

        # Start external mock server
        cls.ext_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockExternalAPIHandler)
        cls.ext_port = cls.ext_server.server_address[1]
        cls.t_ext = threading.Thread(target=cls.ext_server.serve_forever, daemon=True)
        cls.t_ext.start()

        cls.orig_liquid_url = server.LIQUID_API_URL
        cls.orig_typesafe_url = server.TYPESAFE_API_URL
        cls.orig_moondream_url = server.MOONDREAM_API_URL
        server.LIQUID_API_URL = f"http://127.0.0.1:{cls.ext_port}/liquid"
        server.TYPESAFE_API_URL = f"http://127.0.0.1:{cls.ext_port}/typesafe"
        server.MOONDREAM_API_URL = f"http://127.0.0.1:{cls.ext_port}/moondream"

    @classmethod
    def tearDownClass(cls):
        server.LIQUID_API_URL = cls.orig_liquid_url
        server.TYPESAFE_API_URL = cls.orig_typesafe_url
        server.MOONDREAM_API_URL = cls.orig_moondream_url
        cls.ext_server.shutdown()
        cls.ext_server.server_close()
        cls.web_server.shutdown()
        cls.web_server.server_close()
        cls.mock_openbot.shutdown()
        cls.mock_openbot.server_close()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.mock_openbot.logged_in = False
        MockExternalAPIHandler.requests_log.clear()
        MockExternalAPIHandler.liquid_response = None
        MockExternalAPIHandler.liquid_status = 200
        MockExternalAPIHandler.typesafe_response = None
        MockExternalAPIHandler.typesafe_status = 200
        MockExternalAPIHandler.moondream_response = None
        MockExternalAPIHandler.moondream_status = 200

    def test_ansi_to_html(self):
        res = ansi_to_html("\x1b[40m\x1b[37m▀▄ \x1b[0m")
        self.assertIn('background:#000000', res)
        self.assertIn('color:#ffffff', res)
        self.assertIn('▀▄', res)

    def test_get_root_page(self):
        req = urllib.request.urlopen(f"{self.base_url}/")
        self.assertEqual(req.status, 200)
        body = req.read().decode("utf-8")
        self.assertIn("OpenBot", body)
        self.assertIn("thainancaldeira75@gmail.com", body)
        self.assertIn("border-radius: 0", body)
        self.assertIn("Enviar código", body)
        self.assertIn("Entrar", body)

    def test_api_status(self):
        req = urllib.request.urlopen(f"{self.base_url}/api/status")
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["account"], "signed_out")

    def test_api_login_flow(self):
        # 1. Start login
        payload = json.dumps({"email": DEFAULT_EMAIL}).encode("utf-8")
        req_start = urllib.request.Request(
            f"{self.base_url}/api/login/start",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_start) as resp:
            self.assertEqual(resp.status, 200)
            data_start = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data_start["ok"])
            self.assertEqual(data_start["challenge"], "ch_test_1234")

        # 2. Verify with wrong code
        payload_fail = json.dumps({"challenge": "ch_test_1234", "code": "wrong"}).encode("utf-8")
        req_fail = urllib.request.Request(
            f"{self.base_url}/api/login/verify",
            data=payload_fail,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req_fail)
            self.fail("Should have failed with 400")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, 400)
            data_fail = json.loads(err.read().decode("utf-8"))
            self.assertFalse(data_fail["ok"])

        # 3. Verify with right code
        payload_ok = json.dumps({"challenge": "ch_test_1234", "code": "123456"}).encode("utf-8")
        req_ok = urllib.request.Request(
            f"{self.base_url}/api/login/verify",
            data=payload_ok,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_ok) as resp:
            self.assertEqual(resp.status, 200)
            data_ok = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data_ok["ok"])
            self.assertEqual(data_ok["account"], "signed_in")
            self.assertEqual(data_ok["email"], DEFAULT_EMAIL)

        # 4. Mobile connect
        req_mc = urllib.request.Request(f"{self.base_url}/api/mobile-connect", data=b"", method="POST")
        with urllib.request.urlopen(req_mc) as resp:
            self.assertEqual(resp.status, 200)
            data_mc = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data_mc["ok"])
            self.assertIn("span", data_mc["qr_html"])

        # 5. Logout
        req_lo = urllib.request.Request(f"{self.base_url}/api/logout", data=b"", method="POST")
        with urllib.request.urlopen(req_lo) as resp:
            self.assertEqual(resp.status, 200)
            data_lo = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data_lo["ok"])

    def test_form_posts(self):
        # Test regular HTML form submissions
        # POST /login/start
        form_data = urllib.parse.urlencode({"email": DEFAULT_EMAIL}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/login/start",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            html_body = resp.read().decode("utf-8")
            self.assertIn("Código de verificação enviado para", html_body)
            self.assertIn("ch_test_1234", html_body)

        # POST /login/verify
        form_data_verify = urllib.parse.urlencode({"challenge": "ch_test_1234", "code": "123456"}).encode("utf-8")
        req_v = urllib.request.Request(
            f"{self.base_url}/login/verify",
            data=form_data_verify,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req_v) as resp:
            self.assertEqual(resp.status, 200)
            html_body = resp.read().decode("utf-8")
            self.assertIn("Conectado", html_body)

    def test_moondream_key_flow(self):
        with tempfile.TemporaryDirectory() as td:
            key_file = os.path.join(td, "moondream.key")
            orig_path = server.MOONDREAM_KEY_PATH
            server.MOONDREAM_KEY_PATH = key_file
            try:
                # 1. Verifica estado inicial (chave ausente)
                req_get = urllib.request.Request(f"{self.base_url}/moondream")
                with urllib.request.urlopen(req_get) as resp:
                    self.assertEqual(resp.status, 200)
                    html_body = resp.read().decode("utf-8")
                    self.assertIn("Chave ausente", html_body)

                # 2. Salva chave via POST form
                sample_val = "example_val_1234"
                form_data = urllib.parse.urlencode({"key": sample_val}).encode("utf-8")
                req_post = urllib.request.Request(
                    f"{self.base_url}/moondream/save",
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req_post) as resp:
                    self.assertEqual(resp.status, 200)
                    html_body = resp.read().decode("utf-8")
                    self.assertIn("Salva (termina em 1234)", html_body)
                    self.assertNotIn(sample_val, html_body)

                # 3. Verifica se arquivo foi criado com perm 600
                self.assertTrue(os.path.isfile(key_file))
                mode = os.stat(key_file).st_mode & 0o777
                self.assertEqual(mode, 0o600)
                with open(key_file, "r") as f:
                    self.assertEqual(f.read().strip(), sample_val)
            finally:
                server.MOONDREAM_KEY_PATH = orig_path

    def test_config_page_and_liquid_typesafe_keys_flow(self):
        with tempfile.TemporaryDirectory() as td:
            liq_file = os.path.join(td, "liquid.key")
            ts_file = os.path.join(td, "typesafe.key")
            orig_liq = server.LIQUID_KEY_PATH
            orig_ts = server.TYPESAFE_KEY_PATH
            server.LIQUID_KEY_PATH = liq_file
            server.TYPESAFE_KEY_PATH = ts_file
            try:
                # 1. Rota /config exibe pagina com badges de chave ausente e cartao TypeSafe
                req_get = urllib.request.Request(f"{self.base_url}/config")
                with urllib.request.urlopen(req_get) as resp:
                    self.assertEqual(resp.status, 200)
                    html_body = resp.read().decode("utf-8")
                    self.assertIn("Liquid AI", html_body)
                    self.assertIn("TypeSafe", html_body)
                    self.assertNotIn("9Router", html_body)
                    self.assertIn("Decisao reserva Jev (TypeSafe)", html_body)
                    self.assertIn("Chave TypeSafe", html_body)
                    self.assertIn("Chave da API oficial da TypeSafe (modelo jev-latest), usada como reserva quando a Liquid AI falhar.", html_body)
                    # Verifica correcao de estilos dos inputs
                    self.assertIn('input[type="password"]', html_body)
                    self.assertIn("height: 40px", html_body)
                    self.assertIn("padding: 10px", html_body)
                    self.assertIn("border-radius: 0", html_body)

                # 2. Salva chave Liquid AI
                sample_liq = "sk-liquid-test-7788"
                form_liq = urllib.parse.urlencode({"key": sample_liq}).encode("utf-8")
                req_liq = urllib.request.Request(
                    f"{self.base_url}/liquid/save",
                    data=form_liq,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req_liq) as resp:
                    self.assertEqual(resp.status, 200)
                    body = resp.read().decode("utf-8")
                    self.assertIn("termina em 7788", body)
                    self.assertNotIn(sample_liq, body)

                self.assertTrue(os.path.isfile(liq_file))
                mode_liq = os.stat(liq_file).st_mode & 0o777
                self.assertEqual(mode_liq, 0o600)
                with open(liq_file, "r") as f:
                    self.assertEqual(f.read().strip(), sample_liq)

                # 3. Salva chave TypeSafe
                sample_ts = "sk-typesafe-test-5544"
                form_ts = urllib.parse.urlencode({"key": sample_ts}).encode("utf-8")
                req_ts = urllib.request.Request(
                    f"{self.base_url}/typesafe/save",
                    data=form_ts,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req_ts) as resp:
                    self.assertEqual(resp.status, 200)
                    body = resp.read().decode("utf-8")
                    self.assertIn("termina em 5544", body)
                    self.assertIn("Chave TypeSafe salva", body)
                    self.assertNotIn(sample_ts, body)

                self.assertTrue(os.path.isfile(ts_file))
                mode_ts = os.stat(ts_file).st_mode & 0o777
                self.assertEqual(mode_ts, 0o600)
                with open(ts_file, "r") as f:
                    self.assertEqual(f.read().strip(), sample_ts)
            finally:
                server.LIQUID_KEY_PATH = orig_liq
                server.TYPESAFE_KEY_PATH = orig_ts

    def test_api_testar_invalid_params(self):
        # Sem parametro
        req = urllib.request.Request(f"{self.base_url}/api/testar")
        try:
            urllib.request.urlopen(req)
            self.fail("Deveria retornar 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            data = json.loads(exc.read().decode("utf-8"))
            self.assertFalse(data["ok"])
            self.assertIn("ausente", data["erro"])

        # Servico desconhecido
        req_unk = urllib.request.Request(f"{self.base_url}/api/testar?servico=invalid")
        try:
            urllib.request.urlopen(req_unk)
            self.fail("Deveria retornar 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            data = json.loads(exc.read().decode("utf-8"))
            self.assertFalse(data["ok"])
            self.assertIn("desconhecido", data["erro"])

    def test_api_testar_liquid(self):
        # 1. Chave ausente
        with tempfile.TemporaryDirectory() as td:
            orig_liq = server.LIQUID_KEY_PATH
            orig_env = os.environ.pop("LIQUID_API_KEY", None)
            server.LIQUID_KEY_PATH = os.path.join(td, "nonexistent.key")
            try:
                req = urllib.request.Request(f"{self.base_url}/api/testar?servico=liquid")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertFalse(data["ok"])
                    self.assertIsNone(data["http"])
                    self.assertEqual(data["modelo"], "d1-ultra")
                    self.assertIn("não configurada", data["erro"])

                # 2. Sucesso com Liquid
                test_token = "example-mock-liquid-key-1234"
                server.LIQUID_KEY_PATH = os.path.join(td, "liquid.key")
                with open(server.LIQUID_KEY_PATH, "w") as f:
                    f.write(test_token)

                MockExternalAPIHandler.liquid_response = {
                    "model": "d1-ultra",
                    "answers": {
                        "decisao": {
                            "type": "choice",
                            "choice": "clicar_enviar",
                            "confidence": 0.94,
                            "probabilities": {
                                "clicar_enviar": 0.94,
                                "esperar": 0.06,
                            },
                        }
                    },
                }
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["http"], 200)
                    self.assertEqual(data["modelo"], "d1-ultra")
                    self.assertEqual(data["resultado"]["opcao"], "clicar_enviar")
                    self.assertEqual(data["resultado"]["confianca"], 0.94)
                    self.assertIsNone(data["erro"])
                    # Inviolavel: NUNCA vazar a chave
                    self.assertNotIn(test_token, json.dumps(data))

                # Verifica requisicao recebida pelo mock
                self.assertEqual(len(MockExternalAPIHandler.requests_log), 1)
                req_info = MockExternalAPIHandler.requests_log[0]
                self.assertEqual(req_info["path"], "/liquid")
                self.assertEqual(req_info["headers"].get("Authorization"), f"Bearer {test_token}")
                self.assertEqual(req_info["body"]["model"], "d1-ultra")
                self.assertEqual(req_info["body"]["state"], "Tela: botao Enviar visivel, campo de mensagem preenchido. Objetivo: enviar a mensagem.")
                criteria = req_info["body"]["questions"]["decisao"]["criteria"]
                self.assertIn("clicar_enviar", criteria)
                self.assertIn("esperar", criteria)

                # 3. Erro HTTP 401 com tentativa de vazamento no corpo de erro
                MockExternalAPIHandler.requests_log.clear()
                MockExternalAPIHandler.liquid_status = 401
                MockExternalAPIHandler.liquid_response = {"error": f"Invalid token {test_token}"}
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertFalse(data["ok"])
                    self.assertEqual(data["http"], 401)
                    self.assertIsNone(data["resultado"])
                    self.assertIn("401", data["erro"])
                    # Inviolavel: chave DEVE ser sanitizada mesmo no erro retornado pela API
                    self.assertNotIn(test_token, json.dumps(data))
            finally:
                server.LIQUID_KEY_PATH = orig_liq
                if orig_env:
                    os.environ["LIQUID_API_KEY"] = orig_env

    def test_api_testar_typesafe(self):
        with tempfile.TemporaryDirectory() as td:
            orig_ts = server.TYPESAFE_KEY_PATH
            orig_env = os.environ.pop("TYPESAFE_API_KEY", None)
            server.TYPESAFE_KEY_PATH = os.path.join(td, "nonexistent_ts.key")
            try:
                # 1. Chave ausente
                req = urllib.request.Request(f"{self.base_url}/api/testar?servico=typesafe")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertFalse(data["ok"])
                    self.assertIsNone(data["http"])
                    self.assertEqual(data["modelo"], "jev-latest")
                    self.assertIn("não configurada", data["erro"])

                # 2. Sucesso com TypeSafe
                test_token = "example-mock-typesafe-key-5678"
                server.TYPESAFE_KEY_PATH = os.path.join(td, "typesafe.key")
                with open(server.TYPESAFE_KEY_PATH, "w") as f:
                    f.write(test_token)

                MockExternalAPIHandler.typesafe_response = {
                    "model": "jev-latest",
                    "answers": {
                        "decisao": {
                            "type": "choice",
                            "choice": "clicar_enviar",
                            "confidence": 0.91,
                            "probabilities": {
                                "clicar_enviar": 0.91,
                                "esperar": 0.09,
                            },
                        }
                    },
                }
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["http"], 200)
                    self.assertEqual(data["modelo"], "jev-latest")
                    self.assertEqual(data["resultado"]["opcao"], "clicar_enviar")
                    self.assertEqual(data["resultado"]["confianca"], 0.91)
                    self.assertNotIn(test_token, json.dumps(data))

                self.assertEqual(len(MockExternalAPIHandler.requests_log), 1)
                req_info = MockExternalAPIHandler.requests_log[0]
                self.assertEqual(req_info["path"], "/typesafe")
                self.assertEqual(req_info["headers"].get("Authorization"), f"Bearer {test_token}")
                self.assertEqual(req_info["body"]["model"], "jev-latest")
                self.assertEqual(req_info["body"]["state"], "Tela: botao Enviar visivel, campo de mensagem preenchido. Objetivo: enviar a mensagem.")
            finally:
                server.TYPESAFE_KEY_PATH = orig_ts
                if orig_env:
                    os.environ["TYPESAFE_API_KEY"] = orig_env

    def test_api_testar_moondream(self):
        with tempfile.TemporaryDirectory() as td:
            orig_md = server.MOONDREAM_KEY_PATH
            orig_env = os.environ.pop("MOONDREAM_API_KEY", None)
            server.MOONDREAM_KEY_PATH = os.path.join(td, "nonexistent_md.key")
            try:
                # 1. Chave ausente
                req = urllib.request.Request(f"{self.base_url}/api/testar?servico=moondream")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertFalse(data["ok"])
                    self.assertEqual(data["modelo"], "point")
                    self.assertIn("não configurada", data["erro"])

                # 2. Sucesso com Moondream
                test_token = "example-mock-moondream-key-9999"
                server.MOONDREAM_KEY_PATH = os.path.join(td, "moondream.key")
                with open(server.MOONDREAM_KEY_PATH, "w") as f:
                    f.write(test_token)

                MockExternalAPIHandler.moondream_response = {
                    "points": [{"x": 0.5, "y": 0.5}],
                }
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["http"], 200)
                    self.assertEqual(data["modelo"], "point")
                    self.assertEqual(data["resultado"], {"x": 320, "y": 180})
                    self.assertNotIn(test_token, json.dumps(data))

                self.assertEqual(len(MockExternalAPIHandler.requests_log), 1)
                req_info = MockExternalAPIHandler.requests_log[0]
                self.assertEqual(req_info["path"], "/moondream")
                self.assertEqual(req_info["headers"].get("X-Moondream-Auth"), test_token)
                self.assertEqual(req_info["body"]["object"], "Send button")
                img_url = req_info["body"]["image_url"]
                self.assertTrue(img_url.startswith("data:image/png;base64,"))
                # Verifica que a imagem decodificada e valida
                raw_b64 = img_url.split(",")[1]
                png_bytes = base64.b64decode(raw_b64)
                self.assertEqual(png_bytes[:8], b"\x89PNG\r\n\x1a\n")
            finally:
                server.MOONDREAM_KEY_PATH = orig_md
                if orig_env:
                    os.environ["MOONDREAM_API_KEY"] = orig_env

    def test_api_testar_decide(self):
        with tempfile.TemporaryDirectory() as td:
            orig_liq = server.LIQUID_KEY_PATH
            orig_env_liq = os.environ.get("LIQUID_API_KEY")
            orig_env_ts = os.environ.get("TYPESAFE_API_KEY")

            test_token = "example-mock-decide-key-4321"
            server.LIQUID_KEY_PATH = os.path.join(td, "liquid.key")
            with open(server.LIQUID_KEY_PATH, "w") as f:
                f.write(test_token)

            MockExternalAPIHandler.liquid_response = {
                "model": "d1-ultra",
                "answers": {
                    "decisao": {
                        "type": "choice",
                        "choice": "clicar_enviar",
                        "confidence": 0.88,
                        "probabilities": {
                            "clicar_enviar": 0.88,
                            "esperar": 0.12,
                        },
                    }
                },
                "usage": {"cost": 0.0001},
            }

            try:
                req = urllib.request.Request(f"{self.base_url}/api/testar?servico=decide")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["http"], 200)
                    self.assertIn("resultado", data)
                    self.assertEqual(data["resultado"]["opcao"], "clicar_enviar")
                    self.assertNotIn(test_token, json.dumps(data))
            finally:
                server.LIQUID_KEY_PATH = orig_liq
                if orig_env_liq:
                    os.environ["LIQUID_API_KEY"] = orig_env_liq
                if orig_env_ts:
                    os.environ["TYPESAFE_API_KEY"] = orig_env_ts

    def test_config_page_buttons_and_results(self):
        req = urllib.request.Request(f"{self.base_url}/config")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            html_body = resp.read().decode("utf-8")
            # Verifica botoes Testar em cada cartao
            self.assertIn('id="btn-test-liquid"', html_body)
            self.assertIn('id="btn-test-typesafe"', html_body)
            self.assertIn('id="btn-test-moondream"', html_body)
            self.assertIn('id="btn-test-decide"', html_body)
            # Verifica containers de resultado embaixo dos botoes
            self.assertIn('id="result-liquid"', html_body)
            self.assertIn('id="result-typesafe"', html_body)
            self.assertIn('id="result-moondream"', html_body)
            self.assertIn('id="result-decide"', html_body)
            # Verifica classes e estilo
            self.assertIn('.test-result', html_body)
            self.assertIn('border-radius: 0', html_body)
            self.assertIn('testarServico', html_body)
if __name__ == "__main__":
    unittest.main()
