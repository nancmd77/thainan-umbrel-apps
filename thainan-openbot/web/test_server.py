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
                self.wfile.write(b"version=0.61.0\naccount=signed_in\nemail=thainancaldeira75@gmail.com\nserver=online\nserver_name=OpenBot\n")
            else:
                self.wfile.write(b"version=0.61.0\naccount=signed_out\nserver=offline\nserver_name=OpenBot\n")
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
                self.wfile.write(b"version=0.61.0\naccount=signed_in\nemail=thainancaldeira75@gmail.com\nserver=online\nserver_name=OpenBot\n")
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
            self.wfile.write(b"version=0.61.0\naccount=signed_out\nserver=offline\nserver_name=OpenBot\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


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

    @classmethod
    def tearDownClass(cls):
        cls.web_server.shutdown()
        cls.web_server.server_close()
        cls.mock_openbot.shutdown()
        cls.mock_openbot.server_close()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.mock_openbot.logged_in = False

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
                    self.assertIn("chave ausente", html_body)

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
                    self.assertIn("salva (termina em 1234)", html_body)
                    self.assertNotIn(sample_val, html_body)

                # 3. Verifica se arquivo foi criado com perm 600
                self.assertTrue(os.path.isfile(key_file))
                mode = os.stat(key_file).st_mode & 0o777
                self.assertEqual(mode, 0o600)
                with open(key_file, "r") as f:
                    self.assertEqual(f.read().strip(), sample_val)
            finally:
                server.MOONDREAM_KEY_PATH = orig_path


if __name__ == "__main__":
    unittest.main()
