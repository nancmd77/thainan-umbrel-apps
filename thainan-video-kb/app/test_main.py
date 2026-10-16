"""Testes sem rede: python3 -m unittest -v   (dentro de app/)"""
import json
import os
import tempfile
import time
from contextlib import closing
import unittest

import main

ANALISE = {"titulo": "Dica de foco", "resumo": "Resumo.", "pontos_chave": ["a", "b"],
           "tags": ["#Foco", "produtividade", "foco"], "tema": "Produtividade", "transcricao_limpa": "Texto limpo."}


class Fake:
    """Responde por prefixo de URL e grava as chamadas."""

    def __init__(self, fail_llm=0):
        self.calls, self.fail_llm = [], fail_llm
        self.nbs, self.tags, self.analise = [], [], ANALISE  # nbs=None: GET /api/notebooks falha
        self.bm, self.note = [{}], None  # conteúdo do bookmark por GET (lista = respostas em sequência)

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
        if "includeContent=true" in url:
            return {"content": self.bm.pop(0) if len(self.bm) > 1 else self.bm[0]}
        if "/api/v1/bookmarks/" in url and method == "GET":
            return {"note": self.note}
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
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://example.com/x")), (200, {"ok": True, "queued": True}))
        self.assertEqual(main.handle_webhook(ok, body(operation="edited", url="https://tiktok.com/@a/video/1"))[1]["queued"], False)
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://vm.tiktok.com/ZM1/"))[1]["queued"], True)
        self.assertEqual(main.handle_webhook(ok, body(operation="created", url="https://vm.tiktok.com/ZM1")) [1]["queued"], False)  # dup
        self.assertEqual(main.job(2)["bookmark_id"], "b9")
        self.assertEqual(main.handle_webhook(ok, json.dumps({"bookmarkId": "pdf1", "operation": "created", "type": "asset"}).encode())[1]["queued"], True)  # sem URL
        self.assertEqual(main.job(3)["url"], "karakeep:pdf1")
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
        self.assertEqual(main.poll_once(), 2)  # IG + blog novos (qualquer tipo); o antigo fica de fora
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

    # ---------- v1.1: páginas, PDF, texto ----------
    PAGINA = {"type": "link", "url": "https://blog.com/post?id=7&utm_source=x", "title": "Post",
              "crawlStatus": "success", "crawledAt": "2026-10-16", "htmlContent": "<p>" + "Conteúdo útil. " * 30 + "</p><script>x()</script>"}

    def test_pagina_resumo_e_fonte_url(self):
        main.has_media = lambda u: False
        self.fake.bm = [self.PAGINA]
        self.fake.note = "minha nota"
        main.add_job(self.PAGINA["url"])  # colado à mão: cria bookmark
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["kind"]), ("pronto", "pagina"), j["error"])
        self.assertEqual(j["url"], "https://blog.com/post?id=7")  # query real fica, utm sai
        self.assertNotIn("x()", j["data"]["transcript"])
        self.assertNotIn("transcricao_limpa", self.fake.prompt)
        self.assertEqual(self.fake.n("elevenlabs"), 0)
        src = [b for m, u, b in self.fake.calls if u.endswith("/sources/json")][0]
        self.assertEqual((src["type"], src["url"]), ("link", self.PAGINA["url"]))
        patch = [b for m, u, b in self.fake.calls if m == "PATCH"][0]
        self.assertTrue(patch["note"].startswith("minha nota\n\nPontos-chave (Video KB):"))  # nota do nan fica

    def test_nota_reprocessada_nao_duplica(self):
        main.has_media = lambda u: False
        self.fake.bm = [self.PAGINA]
        self.fake.note = "minha nota\n\nPontos-chave (Video KB):\n- velho"
        main.add_job(self.PAGINA["url"], "bk1")
        main.run(1)
        note = [b for m, u, b in self.fake.calls if m == "PATCH"][0]["note"]
        self.assertEqual(note.count("Pontos-chave"), 1)
        self.assertNotIn("velho", note)
        self.assertTrue(note.startswith("minha nota\n\n"))

    def test_crawl_pendente_reagenda_3x(self):
        main.has_media = lambda u: False
        self.fake.bm = [{"type": "link", "url": "https://blog.com/a", "crawlStatus": "pending"}]
        main.add_job("https://blog.com/a", "bkP")
        for n in (1, 2, 3):
            main.run(1)
            j = main.job(1)
            self.assertEqual((j["status"], j["stage"]), ("fila", "texto"))
            self.assertIn(f"tentativa {n}/3", j["error"])
            self.assertFalse(main.q("SELECT id FROM jobs WHERE status='fila' AND after<=?", (time.time(),)))  # espera
        main.run(1)
        self.assertEqual(main.job(1)["status"], "erro")
        self.assertIn("3 tentativas", main.job(1)["error"])
        self.fake.bm = [self.PAGINA]
        main.retry(1)
        main.run(1)
        self.assertEqual(main.job(1)["status"], "pronto")

    def test_pdf_vira_fonte_texto(self):
        self.fake.bm = [{"type": "asset", "assetType": "pdf", "assetId": "a1", "content": "Texto do PDF. " * 30}]
        main.add_job(None, "bkPDF")
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["kind"]), ("pronto", "pdf"), j["error"])
        src = [b for m, u, b in self.fake.calls if u.endswith("/sources/json")][0]
        self.assertEqual(src["type"], "text")
        self.assertIn("Texto do PDF.", src["content"])
        self.assertIn("## Texto", src["content"])

    def test_texto_curto_erro_com_motivo(self):
        self.fake.bm = [{"type": "text", "text": "oi"}]
        main.add_job(None, "bkT")
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["stage"]), ("erro", "texto"))
        self.assertIn("Sem texto suficiente pra resumir (2 caracteres", j["error"])
        self.assertEqual(self.fake.n("chat/completions"), 0)  # não inventa resumo

    def test_imagem_nao_suportada(self):
        self.fake.bm = [{"type": "asset", "assetType": "image", "assetId": "i1"}]
        main.add_job(None, "bkI")
        main.run(1)
        self.assertIn("Tipo não suportado (asset/image)", main.job(1)["error"])

    def test_reddit_com_video_vai_pro_scribe(self):
        main.has_media = lambda u: "reddit" in u
        self.fake.bm = [{"type": "link", "url": "https://www.reddit.com/r/x/comments/1/v/"}]
        main.add_job("https://www.reddit.com/r/x/comments/1/v/", "bkR")
        main.run(1)
        j = main.job(1)
        self.assertEqual((j["status"], j["kind"]), ("pronto", "video"), j["error"])
        self.assertEqual(self.fake.n("elevenlabs"), 1)

    def test_migra_banco_v1_0(self):
        import sqlite3
        main.DATA = tempfile.mkdtemp(dir=self.tmp.name)
        with closing(sqlite3.connect(os.path.join(main.DATA, "kb.db"))) as c:
            c.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, url TEXT UNIQUE, orig TEXT, bookmark_id TEXT, stage TEXT DEFAULT 'novo', "
                      "status TEXT DEFAULT 'fila', error TEXT DEFAULT '', data TEXT DEFAULT '{}', created REAL, updated REAL)")
            c.execute("INSERT INTO jobs(url,orig,created,updated) VALUES('https://tiktok.com/@a/video/1','https://tiktok.com/@a/video/1',0,0)")
            c.commit()
        main.init()
        self.assertEqual(main.q("SELECT kind, after FROM jobs"), [{"kind": "", "after": 0}])
        self.assertIn("Tipo ?", main.queue_page())

    def test_video_longo_nao_transcreve(self):
        main.has_media = lambda u: 3 * 3600
        self.fake.bm = [{"type": "link", "url": "https://youtube.com/watch?v=longo"}]
        main.add_job("https://youtube.com/watch?v=longo", "bkY")
        main.run(1)
        self.assertIn("Vídeo longo (180 min, limite 60 min)", main.job(1)["error"])
        self.assertEqual(self.fake.n("elevenlabs"), 0)

    # ---------- v1.2: coletores ----------
    def ligar(self, **k):
        main.cfg_save({"col_youtube": "", "col_instagram": "", "col_reddit": "", "col_tiktok": "", **k})

    def test_primeira_coleta_so_20_depois_so_novos(self):
        urls = [f"https://www.youtube.com/watch?v={i}" for i in range(25)]
        self.assertEqual(main.ingest(main.cfg(), "youtube:LL", urls), 20)
        self.assertEqual(self.fake.n("/api/v1/bookmarks"), 20)  # bookmark criado no Karakeep
        self.assertEqual(len(main.q("SELECT * FROM seen")), 25)  # os 5 antigos viram "vistos", não entram
        self.assertEqual(main.ingest(main.cfg(), "youtube:LL", ["https://www.youtube.com/watch?v=novo"] + urls), 1)
        self.assertEqual(main.ingest(main.cfg(), "youtube:LL", urls), 0)
        self.assertEqual(main.job(1)["url"], "https://youtube.com/watch?v=0")

    def test_coletores_status_e_cookie_vencido(self):
        self.ligar(col_youtube="1", col_reddit="1", col_instagram="1", col_tiktok="1", yt_playlists="LL", yt_cookies="YT-COOKIE-SECRETO",
                   reddit_feed="https://www.reddit.com/user/u/saved.rss?feed=TOKEN123&user=u", ig_user="@nan", tt_user="nan", tt_cookies="TT-COOKIE")
        chamadas = []

        def tool(args, cookies=None, timeout=900):
            chamadas.append((args, cookies))
            if args[0] == "yt_dlp":
                return "https://www.youtube.com/watch?v=a\nhttps://www.youtube.com/watch?v=b\n"
            if "tiktok.com" in args[-1]:
                return json.dumps([[-1, {"error": "AuthorizationError", "message": "Login required"}]])
            return json.dumps([[2, {"post_shortcode": "AbC"}], [3, "https://cdn/x.jpg", {"post_shortcode": "AbC"}]])
        main.tool = tool
        atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://www.reddit.com/r/x/comments/1/t/"/></entry>'
                b'<entry><link href="https://www.reddit.com/r/y/comments/2/u/"/></entry></feed>')
        orig = self.fake
        main.req = lambda m, u, h=None, b=None, timeout=60, raw=False: atom if "saved.rss" in u else orig(m, u, h, b, timeout, raw)
        main.collect_once()  # sem cookie IG: erro só nesse coletor, os outros seguem
        st = {r["k"]: json.loads(r["v"]) for r in main.q("SELECT * FROM kv WHERE k LIKE 'col:%'")}
        self.assertEqual((st["col:youtube"]["novos"], st["col:reddit"]["novos"]), (2, 2))
        self.assertIn("ig_cookies", st["col:instagram"]["erro"])
        self.assertNotIn("expiraram", st["col:instagram"]["erro"])  # faltando != vencido
        self.assertIn("Cookies expiraram", st["col:tiktok"]["erro"])
        self.assertEqual(chamadas[0], (["yt_dlp", "--flat-playlist", "--print", "url", "--playlist-end", "200", "--no-warnings",
                                        "https://www.youtube.com/playlist?list=LL"], "YT-COOKIE-SECRETO"))
        html_ = main.queue_page()
        self.assertIn("Cookies vencidos", html_)
        self.assertIn("2 novo(s)", html_)
        self.assertNotIn("TOKEN123", html_ + main.config_page())  # feed do Reddit mascarado
        main.cfg_save({"ig_cookies": "IG-COOKIE"})
        main.collect_once()
        self.assertEqual(json.loads(main.q("SELECT v FROM kv WHERE k='col:instagram'")[0]["v"])["novos"], 1)
        self.assertTrue(main.q("SELECT 1 FROM jobs WHERE url='https://instagram.com/p/AbC'"))
        self.assertIn("https://www.instagram.com/nan/saved/", [a[-1] for a, _ in chamadas])  # @ removido

    def test_coletor_desligado_nao_roda(self):
        self.ligar()
        main.tool = lambda *a, **k: self.fail("não devia rodar")
        main.collect_once()
        self.assertEqual(main.q("SELECT * FROM kv WHERE k LIKE 'col:%'"), [])

    def test_config_liga_coletor(self):
        main.save_form({"col_reddit": ["1"], "yt_playlists": [""]})
        self.assertEqual((main.cfg()["col_reddit"], main.cfg()["col_youtube"]), ("1", ""))
        self.assertEqual(main.cfg()["yt_playlists"], ":ytwatchlater\nLL")  # vazio volta ao padrão
        self.assertIn('name="col_reddit" value="1" checked', main.config_page())

    def test_cookie_file_temporario(self):
        with main.cookie_file("abc") as p:
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        self.assertFalse(os.path.exists(p))
        with main.cookie_file("") as p:
            self.assertIsNone(p)
        main.cfg_save({"tt_cookies": "TT", "yt_cookies": "YT"})
        self.assertEqual(main.site_cookies(main.cfg(), "https://vm.tiktok.com/x"), "TT")
        self.assertEqual(main.site_cookies(main.cfg(), "https://youtu.be/x"), "YT")
        self.assertIsNone(main.site_cookies(main.cfg(), "https://blog.com/x"))


if __name__ == "__main__":
    unittest.main()
