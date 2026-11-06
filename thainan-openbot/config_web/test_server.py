#!/usr/bin/env python3
"""
Testes unitarios para o servidor de configuracao Moondream.
"""

import http.server
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

import server


class TestMoondreamConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.key_file = os.path.join(cls.tmp_dir.name, "moondream.key")
        server.KEY_PATH = cls.key_file

        # Inicia servidor HTTP em porta aleatoria livre
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.MoondreamConfigHandler)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp_dir.cleanup()

    def test_save_key_and_permissions(self):
        # 1. Estado inicial
        req = urllib.request.Request(self.base_url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            self.assertIn("chave ausente", body)

        # 2. Salva chave
        sample_val = "example_val_9876"
        form = urllib.parse.urlencode({"key": sample_val}).encode("utf-8")
        req_post = urllib.request.Request(
            f"{self.base_url}/save",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req_post) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            self.assertIn("salva (termina em 9876)", body)
            self.assertNotIn(sample_val, body)

        # 3. Permissao do arquivo (0600)
        self.assertTrue(os.path.isfile(self.key_file))
        mode = os.stat(self.key_file).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        with open(self.key_file, "r") as f:
            self.assertEqual(f.read().strip(), sample_val)


if __name__ == "__main__":
    unittest.main()
