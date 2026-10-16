"""Testes sem rede: python3 -m unittest -v   (dentro de app/)"""
import json
import os
import tempfile
import unittest

import main

ANALISE = {"titulo": "Dica de foco", "resumo": "Resumo.", "pontos_chave": ["a", "b"],
           "tags": ["#Foco", "produtividade", "foco"], "tema": "Produtividade", "transcricao_limpa": "Texto limpo."}


class Fake:
    """Responde por prefixo de URL e grava as chamadas."""

    def __init__(self, fail_llm=0):
        self.calls, self.fail_llm = [], fail_llm
        self.nbs, self.tags, self.analise = [], [], ANALISE  # nbs=None: GET /api/notebooks falha

    def __call__(self, method, url, headers=None, body=None, timeout=60, raw=False):
        self.calls.append((method, url, body))
        if url == "http://cobalt/":
            return {"status": "tunnel", "url": "http://nonve.local:9013/tunnel?id=1", "filename": "a.mp3"}
        if "/tunnel" in url:
            assert url.startswith("http://cobalt/"), url  # host reescrito
            return b"audio-bytes"
        if "elevenlabs" in url:
            assert b"scribe_v2" in body and headers["xi-api-key"] == "EL-KEY-1234"
            return {"text": "ola mundo"}
        if "router/v1/chat/completions" in url:
            if self.fail_llm:
                self.fail_llm -= 1
                raise RuntimeError("HTTP 500")
            self.prompt = body["messages"][0]["content"]
            return {"choices": [{"message": {"content": "```json\n" + json.dumps(self.analise) + "\n```"}}]}
        if url.endswith("/api/v1/tags"):
            return {"tags": [{"id": "t" + t, "name": t} for t in self.tags]}
        if url.endswith("/api/v1/bookmarks") and method == "POST":
            return {"id": "bk1"}
        if "/api/v1/bookmarks/" in url or "/lists/" in url:
            return {}
        if url.endswith("/api/v1/lists"):
            return {"id": "l1"} if method == "POST" else {"lists": []}
        if url.endswith("/api/notebooks"):
            if method == "GET" and self.nbs is None:
                raise RuntimeError("HTTP 502")
            return {"id": "nb1"} if method == "POST" else self.nbs
        if url.endswith("/api/sources/json"):
            return {"id": "src1"}
        raise AssertionError(f"chamada inesperada {method} {url}")

    def n(self, part):
        return sum(part in u for _, u, _ in self.calls)


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        main.DATA = self.tmp.name
        main.init()
        main.cfg_save({"elevenlabs_key": "EL-KEY-1234", "llm_key": "OR-KEY-5678", "llm_url": "http://router/v1", "cobalt_url": "http://cobalt",
                       "karakeep_url": "http://kk", "karakeep_key": "KK-KEY-9999", "notebook_url": "http://nb",
                       "webhook_token": "tok-secreto"})
        self.fake = Fake()
        main.req = self.fake

    def tearDown(self):
        self.tmp.cleanup()

    def test_pipeline_feliz(self):
        main.add_job("https://www.tiktok.com/@x/video/1?utm=a")
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["stage"], j["bookmark_id"]), ("pronto", "pronto", "bk1"))
        self.assertEqual(j["data"]["analysis"]["tags"], ["foco", "produtividade"])  # sem #, minúsculas, sem duplicata
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "tmp")), [])  # áudio apagado
        post_src = [b for m, u, b in self.fake.calls if u.endswith("/sources/json")][0]
        self.assertEqual((post_src["type"], post_src["notebooks"], post_src["embed"]), ("text", ["nb1"], True))
        self.assertIn("Texto limpo.", post_src["content"])
        self.assertIn("Link: https://www.tiktok.com/@x/video/1?utm=a", post_src["content"])

    def test_idempotente(self):
        self.assertTrue(main.add_job("https://www.instagram.com/reel/AbC/"))
        self.assertFalse(main.add_job("https://instagram.com/reel/AbC?igsh=zzz"))
        self.assertEqual(len(main.q("SELECT id FROM jobs")), 1)
        main.run(1)
        n = len(self.fake.calls)
        main.run(1)  # já pronto: nenhuma etapa refeita, nenhuma chamada
        self.assertEqual(len(self.fake.calls), n)

    def test_reprocessar_apos_erro_retoma(self):
        self.fake.fail_llm = 1
        main.add_job("https://www.tiktok.com/@x/video/2")
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["stage"]), ("erro", "resumo"))
        self.assertIn("HTTP 500", j["error"])
        self.assertEqual(j["data"]["transcript"], "ola mundo")
        main.retry(1)
        main.run(1)
        self.assertEqual(main.job(1)["status"], "pronto")
        self.assertEqual(self.fake.n("elevenlabs"), 1)  # não transcreveu de novo
        self.assertEqual(self.fake.n("/tunnel"), 1)  # não baixou de novo

    def test_chave_faltando_e_nunca_vaza(self):
        main.cfg_save({"elevenlabs_key": ""})
        main.add_job("https://www.tiktok.com/@x/video/3")
        main.run(1)
        self.assertIn("elevenlabs_key", main.job(1)["error"])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(main.scrub("falhou com OR-KEY-5678"), "falhou com ***")

    def test_webhook(self):
        ok = {"Authorization": "Bearer tok-secreto"}
        body = lambda **k: json.dumps({"jobId": "j", "bookmarkId": "b9", "userId": "u", "type": "link", **k}).encode()
        self.assertEqual(main.handle_webhook({}, body())[0], 401)
        self.assertEqual(main.handle_webhook({"Authorization": "Bearer errado"}, body())[0], 401)
        self.assertEqual(main.handle_webhook(ok, b"{")[0], 400)
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://example.com/x")), (200, {"ok": True, "queued": False}))
        self.assertEqual(main.handle_webhook(ok, body(operation="edited", url="https://tiktok.com/@a/video/1"))[1]["queued"], False)
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://vm.tiktok.com/ZM1/"))[1]["queued"], True)
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://vm.tiktok.com/ZM1")) [1]["queued"], False)  # dup
        self.assertEqual(main.job(1)["bookmark_id"], "b9")
        main.cfg_save({"webhook_token": ""})
        self.assertEqual(main.handle_webhook(ok, body())[0], 503)

    def test_polling_so_novos(self):
        calls = []
        feed = {"bookmarks": [
            {"id": "n1", "createdAt": "2999-01-01T00:00:00.000Z", "content": {"type": "link", "url": "https://www.instagram.com/reel/NEW/"}},
            {"id": "n2", "createdAt": "2999-01-01T00:00:00.000Z", "content": {"type": "link", "url": "https://blog.com/x"}},
            {"id": "o1", "createdAt": "2000-01-01T00:00:00.000Z", "content": {"type": "link", "url": "https://tiktok.com/@a/video/9"}}]}
        main.req = lambda m, u, *a, **k: calls.append(u) or feed
        self.assertEqual(main.poll_once(), 0)  # 1ª vez só marca o ponto de partida
        self.assertEqual(calls, [])
        main.q("UPDATE kv SET v='2020-01-01T00:00:00' WHERE k='poll_since'")
        self.assertEqual(main.poll_once(), 1)  # só o IG novo
        self.assertEqual(main.poll_once(), 0)  # repetido: idempotente

    def test_config_mascara_e_mantem(self):
        self.assertIn("••••5678", main.config_page())
        self.assertNotIn("OR-KEY-5678", main.config_page())
        main.save_form({"llm_key": [""], "model": ["m/x"]})
        self.assertEqual(main.cfg()["llm_key"], "OR-KEY-5678")  # vazio mantém
        main.save_form({"llm_key": ["-"]})
        self.assertEqual(main.cfg()["llm_key"], "")  # "-" apaga
        self.assertEqual(oct(os.stat(main.cfg_path()).st_mode & 0o777), "0o600")

    def test_reaproveita_caderno_e_tags(self):
        self.fake.nbs = [{"id": "nbX", "name": "Saúde"}, {"id": "nbY", "name": "Finanças"}]
        self.fake.tags = ["Foco", "sono"]
        self.fake.analise = {**ANALISE, "tema": "  saude ", "tags": ["foco", "Sono", "nova tag"]}
        main.add_job("https://www.tiktok.com/@a/video/9")
        main.run(1)
        j = main.job(1)
        self.assertEqual(j["status"], "pronto", j["error"])
        self.assertIn('"Saúde"', self.fake.prompt)  # lista de cadernos no prompt
        self.assertIn('"Foco"', self.fake.prompt)
        self.assertEqual(j["data"]["analysis"]["tema"], "Saúde")
        self.assertEqual(j["data"]["analysis"]["tags"], ["Foco", "sono", "nova tag"])
        src = [b for m, u, b in self.fake.calls if u.endswith("/api/sources/json")][0]
        self.assertEqual(src["notebooks"], ["nbX"])
        self.assertFalse([u for m, u, b in self.fake.calls if m == "POST" and u.endswith("/api/notebooks")])

    def test_sem_lista_segue_e_normaliza(self):
        self.fake.nbs = None  # Open Notebook fora do ar na hora do resumo
        self.fake.analise = {**ANALISE, "tema": "  culinária  rápida "}
        main.add_job("https://www.tiktok.com/@a/video/10")
        main.run(1)
        j = main.job(1)
        self.assertEqual(j["stage"], "notebook")  # resumo passou; parou só no envio ao Open Notebook
        self.assertEqual(j["data"]["analysis"]["tema"], "Culinária rápida")
        self.assertNotIn("Cadernos", self.fake.prompt)


if __name__ == "__main__":
    unittest.main()
