"""Testes sem rede: python3 -m unittest -v   (dentro de app/)"""
import io
import json
import os
import tempfile
import time
from contextlib import closing
import unittest
from unittest import mock

import main

ANALISE = {"titulo": "Dica de foco", "resumo": "Resumo.", "pontos_chave": ["a", "b"],
           "tags": ["#Foco", "produtividade", "foco"], "tema": "Produtividade", "transcricao_limpa": "Texto limpo."}


class Fake:
    """Responde por prefixo de URL e grava as chamadas."""

    def __init__(self, fail_llm=0):
        self.calls, self.fail_llm = [], fail_llm
        self.nbs, self.tags, self.analise = [], [], ANALISE  # nbs=None: GET /api/notebooks falha
        self.bm, self.note, self.no_img = [{}], None, False  # conteúdo do bookmark por GET (lista = respostas em sequência)

    def __call__(self, method, url, headers=None, body=None, timeout=60, raw=False):
        self.calls.append((method, url, body))
        if url == "http://cobalt/":
            return {"status": "tunnel", "url": "http://nonve.local:9013/tunnel?id=1", "filename": "a.mp3"}
        if "/tunnel" in url:
            assert url.startswith("http://cobalt/"), url  # host reescrito
            return b"audio-bytes"
        if "elevenlabs" in url:
            assert b"scribe_v2" in body and headers["xi-api-key"] == "EL-KEY-1234"
            return {"text": "ola mundo", "words": [{"type": "word", "text": "Olá", "start": 0.1, "end": 0.4},
                                                   {"type": "spacing", "text": " "}, {"type": "word", "text": "mundo.", "start": 0.5, "end": 0.9},
                                                   {"type": "spacing", "text": " "}, {"type": "word", "text": "Tchau", "start": 65, "end": 66}]}
        if "router/v1/chat/completions" in url:
            if self.fail_llm:
                self.fail_llm -= 1
                raise RuntimeError("HTTP 500")
            self.prompt, self.user = body["messages"][0]["content"], body["messages"][1]["content"]
            if self.no_img and isinstance(self.user, list):
                raise RuntimeError("POST router/v1/chat/completions -> HTTP 400: image input not supported")
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
                       "webhook_token": "tok-secreto", "kk_on": "1"})  # testes antigos: modo com Karakeep
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

    # ---------- v1.3: visão (crv) ----------
    def fake_crv(self, media, out, n):
        self.assertTrue(os.path.exists(media))  # usa o vídeo já baixado
        self.crv_n = n
        os.makedirs(os.path.join(out, "grids"))
        with open(os.path.join(out, "grids", "grid_01.jpg"), "wb") as f:
            f.write(b"\xff\xd8jpeg")
        with open(os.path.join(out, "frames.json"), "w") as f:
            json.dump({"frames": [{"file": "frame_001.jpg", "timestamp_sec": 0.0}, {"file": "frame_002.jpg", "timestamp_sec": 61.5}]}, f)

    def com_visao(self, crv=None):
        p1 = mock.patch.object(main, "run_crv", crv or self.fake_crv)
        p2 = mock.patch.object(main.shutil, "which", lambda x: "/usr/bin/" + x)
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)

    def test_visao_manda_folhas_e_grava(self):
        self.com_visao()
        self.fake.analise = {**ANALISE, "o_que_aparece": ["00:00: tela com texto \"FOCO\"", "01:01: pessoa aponta pro relógio"]}
        main.cfg_save({"max_frames": "40"})
        main.add_job("https://www.tiktok.com/@x/video/v1")
        main.run(1)
        j = main.job(1)
        self.assertEqual(j["status"], "pronto", j["error"])
        self.assertEqual(self.crv_n, 40)
        self.assertIsNone(j["data"].get("aviso"))
        txt, img = self.fake.user[0]["text"], self.fake.user[1]
        self.assertIn("frame_001.jpg=00:00 frame_002.jpg=01:01", txt)
        self.assertIn("[00:00] Olá mundo.\n[01:05] Tchau", txt)  # transcrição com tempos
        self.assertTrue(img["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("o_que_aparece", self.fake.prompt)
        self.assertEqual(j["data"]["analysis"]["o_que_aparece"][1], "01:01: pessoa aponta pro relógio")
        note = [b for m, u, b in self.fake.calls if m == "PATCH"][0]["note"]
        self.assertIn("O que aparece:\n- 00:00: tela", note)
        src = [b for m, u, b in self.fake.calls if u.endswith("/sources/json")][0]
        self.assertIn("## O que aparece na tela\n- 00:00", src["content"])
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "tmp")), [])  # vídeo e folhas apagados
        cob = [b for m, u, b in self.fake.calls if u == "http://cobalt/"][0]
        self.assertEqual((cob["downloadMode"], cob["videoQuality"]), ("auto", "max"))  # v1.6: curto vem na qualidade original

    def test_modelo_recusa_imagem_segue_so_texto(self):
        self.com_visao()
        self.fake.no_img = True
        main.add_job("https://www.tiktok.com/@x/video/v2")
        main.run(1)
        j = main.job(1)
        self.assertEqual(j["status"], "pronto", j["error"])
        self.assertIn("sem visão: modelo não aceitou as imagens", j["data"]["aviso"])
        self.assertIn("HTTP 400", j["data"]["aviso"])
        self.assertIsInstance(self.fake.user, str)  # 2ª chamada só com texto
        self.assertNotIn("o_que_aparece", self.fake.prompt)
        self.assertIn("sem visão", main.queue_page())

    def test_crv_falha_segue_sem_visao(self):
        def quebra(media, out, n):
            raise RuntimeError("crv: ffmpeg frame extraction failed")
        self.com_visao(quebra)
        main.add_job("https://www.tiktok.com/@x/video/v3")
        main.run(1)
        j = main.job(1)
        self.assertEqual(j["status"], "pronto", j["error"])
        self.assertEqual(j["data"]["aviso"], "sem visão: crv: ffmpeg frame extraction failed")
        self.assertIsInstance(self.fake.user, str)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "tmp")), [])

    def test_sem_ffmpeg_avisa(self):
        with mock.patch.object(main.shutil, "which", lambda x: None):
            main.add_job("https://www.tiktok.com/@x/video/v4")
            main.run(1)
        self.assertEqual(main.job(1)["data"]["aviso"], "sem visão: ffmpeg não instalado")

    def test_fit_grids_reduz(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow não instalado (no container vem com o crv)")
        paths = []
        for i in range(2):
            p = os.path.join(self.tmp.name, f"g{i}.jpg")
            Image.frombytes("RGB", (1440, 1200), os.urandom(1440 * 1200 * 3)).save(p, quality=95)
            paths.append(p)
        total = sum(os.path.getsize(p) for p in paths)
        main.fit_grids(paths, limit=total // 3)
        self.assertLessEqual(sum(os.path.getsize(p) for p in paths), total // 3)

    # ---------- v1.4: SilverBullet ----------
    def sb_job(self, sb):
        main.cfg_save({"sb_url": "http://sb", "sb_token": "SB"})
        main.add_job("https://www.instagram.com/reel/X/", "bkS")
        j = main.job(1)
        j["data"].update({"kind": "video", "analysis": {**ANALISE, "resumo": "R" * 300, "estilo": 2}, "sb": {"midia": True, **sb}})
        self.put = {}
        p = mock.patch.object(main, "sb_put", lambda c, path, data, ctype="": self.put.__setitem__(path, data))
        p.start(), self.addCleanup(p.stop)
        return j

    def test_sb_caminho_absoluto_resumo_inteiro_capa_original(self):
        j = self.sb_job({"video": "Reels/videos/1.mp4", "linha": [{"t": "00:01", "img": "Reels/quadros/1/00.jpg", "txt": "x"}]})
        with mock.patch.object(main, "yt_capa", lambda c, u: b"capa-orig"):
            main.step_silverbullet(j, main.cfg())
        md = self.put["Reels/Dica de foco.md"]
        self.assertEqual(self.put["Reels/capas/1.jpg"], b"capa-orig")
        self.assertIn('cover: ".fs/Reels/capas/1.jpg"\n', md)  # frontmatter continua .fs/ (MediaGallery)
        self.assertIn('video: ".fs/Reels/videos/1.mp4"\n', md)
        self.assertIn('---\n\n${widget.html([==[<!--vkb-reel-->', md)  # v1.7: topo em widget, depois de linha vazia (sb_topo)
        self.assertIn('src=".fs/Reels/videos/1.mp4" poster=".fs/Reels/capas/1.jpg"', md)  # relativo à pasta Reels/: com e sem Path
        self.assertIn("aspect-ratio:9/16", md)
        self.assertIn("<details", md)  # transcrição recolhível
        self.assertIn("Abrir no Instagram", md)
        self.assertIn('<img src=".fs/Reels/quadros/1/00.jpg"', md)
        self.assertNotIn("](.fs/", md)
        self.assertIn(f'resumo: "{"R" * 300}"\n', md)  # sem corte em 240
        self.assertTrue(j["data"]["sb7"] and j["data"]["sb"]["capa_orig"])
        self.put.clear()
        with mock.patch.object(main, "yt_capa", lambda c, u: self.fail("capa já trocada")):
            main.step_silverbullet(j, main.cfg())
        self.assertNotIn("Reels/capas/1.jpg", self.put)

    def test_sb_reel_antigo_sem_yt_dlp_mantem_capa(self):
        j = self.sb_job({"capa": "Reels/capas/1.jpg"})  # pronto do v1.3 (sb6), sem capa_orig
        self.fake.bm = [{}]  # Karakeep sem imagem
        with mock.patch.object(main, "yt_capa", mock.Mock(side_effect=RuntimeError("login required"))) as yt:
            main.step_silverbullet(j, main.cfg())
        yt.assert_called_once()
        self.assertEqual(self.fake.n("includeContent=true"), 1)  # caiu pro Karakeep
        self.assertIn('<img src=".fs/Reels/capas/1.jpg"', self.put["Reels/Dica de foco.md"])
        self.assertEqual(j["data"]["sb"]["capa"], "Reels/capas/1.jpg")

    def test_yt_capa_maior_thumbnail_em_jpeg(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow não instalado (no container vem com o crv)")
        png = io.BytesIO()
        Image.new("RGBA", (4, 4)).save(png, "PNG")
        info = {"thumbnails": [{"url": "http://img/p", "width": 100, "height": 100}, {"url": "http://img/g", "width": 1080, "height": 1920}, {"url": "http://img/s"}]}
        pedidos = []
        main.req = lambda m, u, h=None, b=None, timeout=60, raw=False: pedidos.append(u) or png.getvalue()
        with mock.patch.object(main.subprocess, "run", lambda *a, **k: mock.Mock(stdout=json.dumps(info) + "\n")):
            jpg = main.yt_capa(main.cfg(), "https://www.instagram.com/reel/X/")
        self.assertEqual(pedidos, ["http://img/g"])
        self.assertTrue(jpg.startswith(b"\xff\xd8"))

    # ---------- v1.6.2: mural (sem Excalidraw) ----------
    def sb_fake(self):
        main.cfg_save({"sb_url": "http://sb", "sb_token": "SB"})
        self.sb = {"Video KB.md": b"# Video KB\n\n## Assuntos\n${mural()}\n"}
        for nome, f in (("sb_get", lambda c, p: self.sb.get(p)), ("sb_put", lambda c, p, d, t="": self.sb.__setitem__(p, d if isinstance(d, bytes) else d.encode()))):
            p = mock.patch.object(main, nome, f)
            p.start(), self.addCleanup(p.stop)

    def reel(self, tema, titulo, video=True, capa=True):
        main.add_job(f"https://www.instagram.com/reel/{titulo}/")
        j = main.job(main.q("SELECT max(id) AS i FROM jobs")[0]["i"])
        sb = {"page": "Reels/" + titulo, "t": time.time()}
        if capa:
            sb["capa"] = f"Reels/capas/{j['id']}.jpg"
        if video:
            sb["video"] = f"Reels/videos/{j['id']}.mp4"
        j["data"].update(kind="video", analysis={**ANALISE, "tema": tema, "titulo": titulo, "gancho": "Gancho curto."}, sb=sb)
        main.save(j)

    def test_mural_card_com_capa_e_video_e_troca_excalidraw(self):
        self.sb_fake()
        bloco = "```excalidraw\nurl:Video KB/Quadro.excalidraw\nheight:750px\ntheme:dark\n```\n\n"
        self.sb["index.md"] = ("# Meus salvos\n\n" + bloco + "## Assuntos\nfim\n").encode()
        self.sb["Video KB/Quadro.excalidraw"] = b"{}"
        self.reel("Foco", "Com video")
        self.reel("Foco", "So capa {x}", video=False)
        self.reel("Foco", "Nada", video=False, capa=False)
        main.sb_mural(main.cfg())
        for pg in ("index.md", "Video KB.md"):
            t = self.sb[pg].decode()
            self.assertNotIn("excalidraw", t)
            self.assertTrue(t.startswith("\n# "))
            self.assertIn('<a class="vkb-card" href="Reels/Com%20video"', t)  # v1.7: card todo abre a página
            self.assertIn('<img src=".fs/Reels/capas/1.jpg"', t)  # capa relativa: resolve com/sem Path
            self.assertIn("▶", t)  # selo de vídeo
            self.assertIn('<img src=".fs/Reels/capas/2.jpg"', t)  # sem vídeo: capa
            self.assertIn("So capa &#123;x&#125;", t)  # chaves não quebram o ${...}
            self.assertEqual(t.count("]==])}\n\n## Assuntos"), 1)
        self.assertEqual(self.sb["Video KB/Quadro.excalidraw"], b"{}")  # arquivo do nan intacto
        self.reel("Foco", "Novo")
        main.sb_mural(main.cfg())
        t = self.sb["index.md"].decode()
        self.assertEqual(t.count("<!--vkb-->"), 1)  # troca, não duplica
        self.assertIn("Novo", t)

    def test_mural_links_relativos_resolvem_com_path(self):
        self.sb_fake()
        self.sb["index.md"] = b"# Meus salvos\n\n## Assuntos\nfim\n"
        self.reel("Foco", "Video Um")
        main.sb_mural(main.cfg())
        t = self.sb["index.md"].decode()
        # URLs relativas resolvem com e sem Path (ex.: /cerebro)
        self.assertIn('src=".fs/Reels/capas/1.jpg"', t)
        self.assertIn('href="Reels/Video%20Um"', t)
        self.assertNotIn('src="/.fs/', t)
        self.assertNotIn('href="/Reels/', t)

    def test_init_marca_mural_sujo_v1_6_3(self):
        main.q("DELETE FROM kv WHERE k IN ('v1.6.3', 'mural_sujo')")
        main.init()
        self.assertTrue(main.q("SELECT 1 FROM kv WHERE k='mural_sujo' AND v='1'"))
        self.assertTrue(main.q("SELECT 1 FROM kv WHERE k='v1.6.3' AND v='1'"))
        main.q("UPDATE kv SET v='0' WHERE k='mural_sujo'")
        main.init()
        # Idempotente: não remarca se já migrou
        self.assertEqual(main.q("SELECT v FROM kv WHERE k='mural_sujo'")[0]["v"], "0")
    def test_regrava_todos_v18_uma_vez(self):
        j = self.sb_job({"video": "Reels/videos/1.mp4", "page": "Reels/Dica de foco", "capa": "Reels/capas/1.jpg"})
        j["data"]["sb7"] = True
        main.save(j)
        put = {}
        info = {"timestamp": 1760000000, "uploader": "fulano", "view_count": 1234567, "like_count": 45210, "comment_count": 320}
        with mock.patch.object(main, "sb_put", lambda c, p, b, *_: put.__setitem__(p, b)), mock.patch.object(main, "yt_info", lambda c, u: info):
            main.sb_regrava_todos(main.cfg())
        md = put["Reels/Dica de foco.md"]
        self.assertIn("<!--vkb-reel-->", md)
        self.assertIn("1,2 mi", md)
        self.assertIn("45,2 mil", md)
        self.assertIn("Postado 09/10/2025", md)
        self.assertIn('views: 1234567', md)  # frontmatter pra Lua dos assuntos
        self.assertEqual(main.job(1)["data"]["meta"]["autor"], "fulano")
        self.assertTrue(main.q("SELECT 1 FROM kv WHERE k='sb_v183'"))

    def test_humor_ignorado_e_guardar_mesmo_assim(self):
        main.add_job("https://www.instagram.com/reel/MEME1/")
        jid = main.q("SELECT max(id) m FROM jobs")[0]["m"]
        j = main.job(jid)
        j["data"] = {"kind": "video", "transcript": "kkk", "dur": 10}
        main.save(j)
        resp = {"choices": [{"message": {"content": main.json.dumps({"titulo": "Gato caindo", "resumo": "x", "tema": "Humor", "humor": True})}}]}
        with mock.patch.object(main, "llm", lambda *a, **k: resp), mock.patch.object(main, "sb_tags_existentes", lambda: []):
            main.cfg_save({"llm_url": "http://x", "llm_key": "k", "pular_humor": "1"})
            j = main.job(jid)
            with self.assertRaises(main.Ignorado):
                main.step_resumo(j, main.cfg())
        j = main.job(jid); j.update(status="ignorado"); main.save(j)
        main.reprocessar_item(jid)
        self.assertEqual((main.job(jid)["status"], main.job(jid)["data"]), ("fila", {"forcar": True}))

    def test_post_so_de_foto_ignorado(self):
        main.add_job("https://www.instagram.com/p/FOTO1/")
        jid = main.q("SELECT max(id) m FROM jobs")[0]["m"]
        j = main.job(jid)
        main.cfg_save({"elevenlabs_key": "k", "pular_humor": "1"})
        def falha(*a): raise RuntimeError("Cobalt: picker | yt-dlp: ERROR: [Instagram] FOTO1: No video formats found!")
        with mock.patch.object(main, "baixar", falha):
            with self.assertRaises(main.Ignorado):
                main.step_audio(j, main.cfg())

    def test_mural_depois_dos_assuntos_e_previa(self):
        s = {"page": "Reels/X", "capa": "Reels/x.jpg", "linha": [{"img": f"Reels/x-{n}.jpg", "t": "0:0", "txt": ""} for n in range(9)]}
        card = main.mural_card("Reels/X", {"titulo": "T", "gancho": "G", "resumo": "Resumo R", "tema": "A"}, s, "2026-10-17", {"postado": "2026-10-15"})
        self.assertIn('class="vkb-card"', card)
        self.assertEqual(card.count('animation-delay'), 6)
        self.assertIn("Resumo R", card)
        self.assertIn(">Postado</div>", card)
        self.assertIn("15/10/2026", card)
        txt = "# Meus salvos\n\n${widget.html([==[<!--vkb-->velho]==])}\n\n" + main.ASSUNTOS + "\n"
        base = main.MURAL.sub("", txt)
        self.assertIn(main.ASSUNTOS, base)

    def test_num_br_e_metricas_sem_inventar(self):
        self.assertEqual([main.num_br(n) for n in (999, 1000, 1250, 1_500_000, 2_000_000_000)], ["999", "1 mil", "1,2 mil", "1,5 mi", "2 bi"])
        self.assertEqual(main.metricas_html({}), "")
        m = main.meta_de_info({"upload_date": "20261001", "view_count": None, "like_count": 10})
        self.assertEqual((m["postado"], m.get("views"), m["likes"]), ("2026-10-01", None, 10))

    def test_mural_desligado_so_tira_o_bloco(self):
        self.sb_fake()
        self.reel("Foco", "A")
        main.sb_mural(main.cfg())
        main.cfg_save({"mural_on": ""})
        main.sb_mural(main.cfg())
        self.assertNotIn("<!--vkb-->", self.sb["Video KB.md"].decode())
        self.assertIn("## Assuntos", self.sb["Video KB.md"].decode())

    def test_sb_topo(self):
        self.assertEqual(main.sb_topo("---\na: 1\n---\n![](/v.mp4)\n"), "---\na: 1\n---\n\n![](/v.mp4)\n")
        self.assertEqual(main.sb_topo("# T\n"), "\n# T\n")
        self.assertEqual(main.sb_topo("\n# T\n"), "\n# T\n")
        self.sb_fake()
        i = self.reel("Foco", "A")
        main.q("UPDATE jobs SET data=replace(data, '\"analysis\"', '\"sb7\": true, \"analysis\"')")
        self.sb["Reels/A.md"] = b"---\ntype: reel\n---\n![](/Reels/videos/1.mp4)\n"
        self.sb["Temas/Foco.md"] = b"# Foco\n"
        main.sb_topo_todos(main.cfg())
        self.assertEqual(self.sb["Reels/A.md"], b"---\ntype: reel\n---\n\n![](/Reels/videos/1.mp4)\n")
        self.assertEqual(self.sb[main.pasta(main.cfg(), "sb_temas") + "/Foco.md"], b"\n# Foco\n")
        self.assertNotIn("index.md", self.sb)  # não cria página que não existe
        self.assertTrue(main.q("SELECT 1 FROM kv WHERE k='sb_topo'"))

    def test_inicio_refila_prontos_sem_marca(self):
        main.add_job("https://www.tiktok.com/@x/video/r1")
        main.q("UPDATE jobs SET status='pronto'")
        main.sb_refila()
        self.assertEqual(main.job(1)["status"], "pronto")  # SB desligado: nada
        main.cfg_save({"sb_url": "http://sb", "sb_token": "SB"})
        main.sb_refila()
        self.assertEqual(main.job(1)["status"], "fila")
        self.assertTrue(main.q("SELECT 1 FROM kv WHERE k='mural_sujo' AND v='1'"))

    # ---------- v1.6: vídeo e configurações ----------
    def test_plano_video_comprimir_ou_nao(self):
        c, MB = main.cfg(), main.MB
        self.assertEqual(main.plano_video(c, 59 * MB, 90), ("enviar", 60 * MB, None))  # curto que cabe: original, sem reencodar
        self.assertEqual(main.plano_video(c, 61 * MB, 90), ("comprimir", 60 * MB, None))  # curto grande: comprime, mantém resolução
        self.assertEqual(main.plano_video(c, 61 * MB, 0), ("comprimir", 60 * MB, None))  # duração desconhecida (IG/TikTok) = curto
        self.assertEqual(main.plano_video(c, 10 * MB, 181)[0], "link")  # longo, padrão: não envia
        main.cfg_save({"longo_politica": "comprimir"})
        c = main.cfg()
        self.assertEqual(main.plano_video(c, 500 * MB, 600), ("comprimir", 200 * MB, 720))
        self.assertEqual(main.plano_video(c, 150 * MB, 600), ("enviar", 200 * MB, 720))

    def test_qualidade_do_download(self):
        c = main.cfg()
        self.assertEqual(main.qual_download(c, 0), "max")
        self.assertEqual(main.qual_download(c, 3600), "720")  # longo: teto da resolução de longos
        main.cfg_save({"dl_qualidade": "480"})
        self.assertEqual(main.qual_download(main.cfg(), 3600), "480")  # nunca acima do pedido

    def test_bitrate_alvo(self):
        self.assertEqual(main.bitrate_alvo(60 * main.MB, 120), 3762)  # 60 MB em 2 min: 3858 kbps com margem de 8%, menos 96 de áudio
        self.assertEqual(main.bitrate_alvo(1 * main.MB, 3600), 100)  # impossível caber: piso

    def test_comprimir_nunca_aumenta(self):
        src, dst = os.path.join(self.tmp.name, "a.mp4"), os.path.join(self.tmp.name, "b.mp4")
        with open(src, "wb") as f:
            f.write(b"x" * 1000)
        tam = [0]
        def ffmpeg(cmd, **k):
            with open(cmd[-1], "wb") as f:
                f.write(b"y" * tam[0])
            return mock.Mock(returncode=0)
        with mock.patch.object(main.shutil, "which", lambda x: "/usr/bin/ffmpeg"), mock.patch.object(main.subprocess, "run", ffmpeg):
            tam[0] = 1200
            self.assertIsNone(main.comprimir(src, dst, 10_000, 10))  # saiu maior que o original
            tam[0] = 900
            self.assertIsNone(main.comprimir(src, dst, 800, 10))  # menor, mas não coube no limite
            self.assertEqual(main.comprimir(src, dst, 950, 10), dst)
        self.assertIsNone(main.comprimir(src, dst, 950, 0))  # sem duração não dá pra calcular bitrate

    def test_valida_numeros(self):
        self.assertEqual(main.valida("curto_mb", " 80 "), "80")
        for ruim in ("abc", "4", "2001", "1.5", ""):
            with self.assertRaises(ValueError):
                main.valida("curto_mb", ruim)
        erros = main.save_form({"curto_mb": ["9999"], "tl_quadros": ["8"], "dl_ordem": ["xpto"], "model": ["m/x"]})
        c = main.cfg()
        self.assertEqual((c["curto_mb"], c["tl_quadros"], c["dl_ordem"], c["model"]), ("60", "8", "cobalt", "m/x"))  # inválido fica como estava
        self.assertTrue(any("de 5 a 2000" in x for x in erros))
        self.assertTrue(any("Ordem do download" in x for x in erros))
        self.assertIn('type="number"', main.config_page())

    def test_assuntos_fixos(self):
        main.cfg_save({"assuntos_fixos": "Saúde\nFinanças\n"})
        self.fake.analise = {**ANALISE, "tema": "saude"}
        main.add_job("https://www.tiktok.com/@x/video/f1")
        main.run(1)
        self.assertEqual(main.job(1)["data"]["analysis"]["tema"], "Saúde")
        self.assertIn('Assuntos permitidos: ["Saúde", "Finanças"]', self.fake.prompt)
        self.assertNotIn("Cadernos", self.fake.prompt)
        self.assertEqual(main.tema_fixo("Culinária", ["Saúde", "Finanças"]), "Saúde")  # fora da lista: primeiro

    def test_estilo_extra_so_soma(self):
        main.cfg_save({"estilo_extra": "use emoji no gancho", "resumo_frases": "6"})
        main.add_job("https://www.tiktok.com/@x/video/e1")
        main.run(1)
        self.assertIn(main.ESTILO, self.fake.prompt)
        self.assertIn("use emoji no gancho", self.fake.prompt)
        self.assertIn('"resumo" (2 a 6 frases)', self.fake.prompt)

    def test_export_sem_segredos_e_import(self):
        main.cfg_save({"ig_cookies": "IG-COOKIE-XYZ", "reddit_feed": "https://reddit/feed?TOKEN", "curto_mb": "80"})
        exp = main.exportar()
        dump = json.dumps(exp)
        for segredo in ("OR-KEY-5678", "EL-KEY-1234", "KK-KEY-9999", "tok-secreto", "IG-COOKIE-XYZ", "TOKEN"):
            self.assertNotIn(segredo, dump)
        self.assertEqual(exp["curto_mb"], "80")
        erros = main.importar(json.dumps({**exp, "curto_mb": 120, "mural_on": False, "llm_key": "ROUBADA", "tl_largura": "9", "x": 1}))
        c = main.cfg()
        self.assertEqual((c["curto_mb"], c["mural_on"], c["llm_key"], c["tl_largura"]), ("120", "", "OR-KEY-5678", "360"))
        self.assertEqual(len(erros), 1)  # só tl_largura fora da faixa
        with self.assertRaises(ValueError):
            main.importar("[1]")

    def test_config_antiga_continua_valendo(self):
        with open(main.cfg_path(), "w") as f:
            json.dump({"llm_key": "K", "model": "m/velho", "max_frames": "40", "max_min": "abc", "col_reddit": "1"}, f)
        c = main.cfg()
        self.assertEqual((c["model"], main.num(c, "max_frames"), main.num(c, "max_min"), c["col_reddit"]), ("m/velho", 40, 60, "1"))
        self.assertEqual((c["kk_on"], c["nb_on"], c["sb_on"], c["sintese_on"], c["sb_reels"], main.num(c, "curto_mb")), ("0", "1", "1", "1", "Reels", 60))  # v1.8: Karakeep desligado por padrão
        self.assertEqual(main.steps({"kind": "video"})[-1:], ["notebook"])

    def test_sem_karakeep_pagina_lida_direto(self):
        main.cfg_save({"kk_on": "0"})
        main.add_job("https://blog.exemplo.com/post")
        j = main.job(1)
        h = ('<html><head><title>T</title><meta property="og:title" content="Título bom"><meta property="og:image" content="/capa.jpg"></head>'
             '<body><nav>menu menu</nav><article><p>' + "conteúdo útil " * 40 + '</p></article></body></html>')
        with mock.patch.object(main, "has_media", lambda u: 0), mock.patch.object(main, "baixa_url", lambda u, *a: ("text/html", h.encode())):
            main.step_tipo(j, main.cfg())
            main.step_texto(j, main.cfg())
        self.assertEqual(j["data"]["kind"], "pagina")
        self.assertTrue(j["data"]["transcript"].startswith("Título bom\n\nconteúdo útil"))
        self.assertNotIn("menu", j["data"]["transcript"])
        self.assertEqual(j["data"]["og_image"], "https://blog.exemplo.com/capa.jpg")
        self.assertFalse(any("kk" in str(c) for c in self.fake.calls))  # nada pro Karakeep

    def test_sem_karakeep_pdf_e_coletor_direto(self):
        main.cfg_save({"kk_on": "0"})
        main.add_job("https://x.com/a/doc.pdf")
        j = main.job(1)
        main.step_tipo(j, main.cfg())
        self.assertEqual(j["data"]["kind"], "pdf")
        self.assertEqual(main.ingest(main.cfg(), "reddit:saved", ["https://reddit.com/r/a/1"]), 1)
        self.assertIsNone(main.job(2)["bookmark_id"])
        self.assertEqual(main.poll_once(), 0)
        self.assertNotIn("karakeep", main.steps({"kind": "video"}))

    def test_init_v1_8_desliga_karakeep_uma_vez(self):
        main.q("DELETE FROM kv WHERE k='v1.8.0'")
        main.init()
        self.assertEqual(main.cfg()["kk_on"], "0")
        main.cfg_save({"kk_on": "1"})
        main.init()
        self.assertEqual(main.cfg()["kk_on"], "1")  # nan religou: fica

    def test_reprocessar_so_silverbullet(self):
        main.add_job("https://www.tiktok.com/@x/video/s1")
        j = main.job(1)
        j["data"].update(analysis=ANALISE, sb7=True, sb={"midia": True, "video": "Reels/videos/1.mp4", "capa": "Reels/capas/1.jpg"})
        j["status"] = "pronto"
        main.save(j)
        main.reprocessar(True)
        d = main.job(1)
        self.assertEqual(d["status"], "fila")
        self.assertNotIn("sb7", d["data"])
        self.assertEqual(d["data"]["sb"], {"capa": "Reels/capas/1.jpg"})  # vídeo e quadros refeitos; capa fica
        self.assertEqual(d["data"]["analysis"], ANALISE)

    # ---------- v1.7: tela estilo Linkding ----------
    def itens_demo(self):
        for url, titulo, tema, tags, status, trans in (
                ("https://www.instagram.com/reel/A/", "Dormir melhor", "Saúde", ["sono", "rotina"], "pronto", "dormir cedo ajuda"),
                ("https://www.youtube.com/watch?v=b", "Planilha de gastos", "Finanças", ["planilha"], "pronto", "gastos fixos"),
                ("https://blog.com/c", "Treino curto", "Saúde", ["treino"], "erro", "")):
            main.add_job(url)
            j = main.job(main.q("SELECT max(id) AS i FROM jobs")[0]["i"])
            j["data"].update(analysis={**ANALISE, "titulo": titulo, "tema": tema, "tags": tags, "transcricao_limpa": trans})
            j["status"] = status
            main.save(j)

    def test_busca_e_filtros(self):
        self.itens_demo()
        its = main.itens()
        ids = lambda **p: [i["id"] for i in main.filtrar(its, p)]
        self.assertEqual(ids(tag="Rotina"), [1])  # tag sem diferenciar maiúscula
        self.assertEqual(ids(q="#treino"), [3])  # "#tag" na busca filtra tag
        self.assertEqual(ids(tema="saude"), [3, 1])  # tema sem acento
        self.assertEqual(ids(status="erro"), [3])
        self.assertEqual(ids(fonte="youtube"), [2])
        self.assertEqual(ids(q="DORMIR"), [1])  # busca na transcrição
        self.assertEqual(ids(q="gastos inexistente"), [])  # todas as palavras
        self.assertEqual(ids(tema="Saúde", status="pronto"), [1])
        html_ = main.queue_page("", {"tag": "planilha"})
        self.assertIn("Planilha de gastos", html_)
        self.assertNotIn("Treino curto", html_)
        self.assertIn('href="/?tema=Sa%C3%BAde"', html_)  # lateral: assunto clicável
        self.assertIn("Limpar filtros", html_)

    def test_lista_vazia_e_com_itens(self):
        self.assertIn("Nenhum item ainda", main.queue_page())
        self.itens_demo()
        html_ = main.queue_page()
        self.assertEqual(html_.count('<li class="item">'), 3)
        self.assertIn('Tentar de novo', html_)  # item com erro retoma
        self.assertIn('action="/item/1/reprocessar"', html_)
        self.assertIn("Nada encontrado", main.queue_page("", {"q": "zzz"}))

    def test_tela_sem_segredos(self):
        main.cfg_save({"sb_url": "http://sb", "sb_token": "SB-TOKEN-XYZ", "ig_cookies": "IG-COOKIE-XYZ", "reddit_feed": "https://r/feed?TOKEN123"})
        self.itens_demo()
        html_ = main.queue_page() + main.config_page()
        for segredo in ("OR-KEY-5678", "EL-KEY-1234", "KK-KEY-9999", "tok-secreto", "SB-TOKEN-XYZ", "IG-COOKIE-XYZ", "TOKEN123"):
            self.assertNotIn(segredo, html_)

    def test_tema_manual(self):
        self.assertIn('<html lang="pt-BR"><head>', main.queue_page())  # automático: sem data-tema, segue o sistema
        main.cfg_save({"ui_tema": "escuro"})
        self.assertIn('<html lang="pt-BR" data-tema="escuro">', main.queue_page())



class ModelosHigh(unittest.TestCase):
    def test_alto(self):
        self.assertEqual(main.alto("ag/gemini-3.8-flash-medium"), "ag/gemini-3.8-flash-high")
        self.assertEqual(main.alto("ag/gemini-3.1-pro-low"), "ag/gemini-3.1-pro-high")
        self.assertEqual(main.alto("openrouter/outro"), "openrouter/outro")

    def test_padrao_links_pro_high(self):
        self.assertEqual(main.DEFAULTS["model_links"], "ag/gemini-3.8-flash-high")
        self.assertTrue(main.DEFAULTS["model"].endswith("-high"))

    def test_modelo_faltando(self):
        c = {"llm_url": "http://x/v1", "llm_key": "k", "model": "ag/a-high", "model_links": "ag/b-high"}
        with mock.patch.object(main, "req", return_value={"data": [{"id": "ag/a-high"}]}):
            with self.assertRaisesRegex(RuntimeError, "ag/b-high"):
                main.modelos_ok(c)

    def test_user_agent_padrao(self):
        vistos = {}
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b"{}"
        def fake(rq, timeout=0):
            vistos.update(rq.headers)
            return R()
        with mock.patch("urllib.request.urlopen", fake):
            main.req("GET", "https://api.hikerapi.com/sys/balance", {"x-access-key": "k"})
        self.assertIn("Mozilla", vistos.get("User-agent", ""))

class TSteam(unittest.TestCase):
    def test_jogo_busca_na_steam(self):
        def fake(m, u, h=None, b=None, timeout=60, raw=False):
            if "steampowered" in u:
                return {"items": [{"type": "app", "name": "Grand Theft Auto IV: The Complete Edition", "id": 12210, "platforms": {"windows": True}}]}
            return {"results": []}
        with mock.patch.object(main, "req", fake):
            cs = main.candidatos({"searx_url": "http://s"}, {"nome": "Grand Theft Auto IV", "tipo": "jogo"}, [])
        self.assertEqual(cs[0]["url"], "https://store.steampowered.com/app/12210/")
        self.assertIn("(PC)", cs[0]["titulo"])


class TDecider(unittest.TestCase):
    """v1.11: Decider confere escolhas fechadas; fora do ar = vale o Gemini."""
    C = {"llm_url": "http://router/v1", "llm_key": "k"}

    def test_ordem_e_reserva(self):
        vistos = []
        def fake(m, u, h=None, b=None, timeout=60, raw=False):
            vistos.append(b["model"])
            if "pplx" in b["model"]:
                raise RuntimeError("HTTP 503")
            assert u == "http://router/v1/systemone"
            return {"model": "typesafe/jev", "answers": {"q": {"type": "noul", "noul": 0.9}}}
        with mock.patch.object(main, "req", fake):
            self.assertTrue(main.decider_sim(self.C, "t", {}, "?", "s", "n"))
        self.assertEqual(vistos, list(main.DECIDER_MODELOS[:2]))

    def test_incerto_e_fora_do_ar(self):
        with mock.patch.object(main, "req", return_value={"answers": {"q": {"noul": 0.7}}}):
            self.assertFalse(main.decider_sim(self.C, "t", {}, "?", "s", "n"))  # 70% < 85%
        with mock.patch.object(main, "req", side_effect=RuntimeError("HTTP 500")):
            self.assertIsNone(main.decider_sim(self.C, "t", {}, "?", "s", "n"))
        self.assertIsNone(main.decider_sim({**self.C, "decider_on": "0"}, "t", {}, "?", "s", "n"))

    def test_choice_usa_probabilidade(self):
        r = {"answers": {"q": {"choice": "1", "probabilities": {"0": 0.05, "1": 0.9}, "confidence": 0.2}}}
        with mock.patch.object(main, "req", return_value=r):
            d = main.decidir(self.C, "t", {}, {"type": "choice", "instructions": "?", "criteria": {"0": "a", "1": "b"}})
        self.assertEqual((d["valor"], d["prob"]), ("1", 0.9))
        with mock.patch.object(main, "req", return_value={"answers": {"q": {"choice": "9"}}}):
            self.assertIsNone(main.decidir(self.C, "t", {}, {"type": "choice", "instructions": "?", "criteria": {"0": "a"}}))


if __name__ == "__main__":
    unittest.main()


HIKER_MEDIA = {"pk": "1", "code": "AbC", "taken_at": "2026-10-01T12:00:00Z", "media_type": 2, "user": {"username": "criador", "full_name": "Criador X"},
               "caption_text": "Legenda com https://github.com/t4t5/omdrop-owl", "play_count": 12345, "like_count": 678, "comment_count": 9,
               "thumbnail_url": "https://cdn.ig/thumb.jpg", "video_url": "https://cdn.ig/v.mp4", "video_duration": 42.0}


class TInstagram(unittest.TestCase):
    """v1.10: HikerAPI pra cada Reel, login próprio só nos salvos, cookies de reserva."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        main.DATA = self.tmp.name
        main.init()
        main.IG_INFO.clear()
        main.IG.update(cl=None, chave=None)
        self.calls = []

        def fake(method, url, headers=None, body=None, timeout=60, raw=False):
            self.calls.append(url)
            if url.startswith("https://api.hikerapi.com/v1/media/by/url"):
                assert headers["x-access-key"] == "HK-KEY-1234"
                return dict(HIKER_MEDIA)
            if url == "https://cdn.ig/v.mp4":
                return b"video"
            raise AssertionError(url)
        main.req = fake

    def tearDown(self):
        self.tmp.cleanup()

    def test_norm_hiker(self):
        i = main.ig_norm(HIKER_MEDIA)
        self.assertEqual((i["channel"], i["view_count"], i["like_count"], i["video_url"]), ("criador", 12345, 678, "https://cdn.ig/v.mp4"))
        m = main.meta_de_info(i)
        self.assertEqual((m["autor"], m["views"], m["postado"][:7]), ("Criador X (@criador)", 12345, "2026-10"))
        self.assertIsNone(main.ig_norm({**HIKER_MEDIA, "like_and_view_counts_disabled": True})["like_count"])  # escondido = sem número

    def test_info_e_video_pela_hiker_sem_login(self):
        main.cfg_save({"hiker_key": "HK-KEY-1234"})
        c = main.cfg()
        self.assertTrue(main.ig_on(c, "https://www.instagram.com/reel/AbC/"))
        self.assertFalse(main.ig_on(c, "https://www.tiktok.com/@x/video/1"))
        self.assertIn("omdrop-owl", main.yt_info(c, "https://www.instagram.com/reel/AbC/")["description"])
        os.makedirs(os.path.join(self.tmp.name, "tmp"), exist_ok=True)
        path = main.baixar(c, "https://www.instagram.com/reel/AbC/", os.path.join(self.tmp.name, "tmp", "1"), "max")
        self.assertEqual(open(path, "rb").read(), b"video")
        self.assertEqual(sum(u.startswith("https://api.hikerapi.com") for u in self.calls), 1)  # cache: uma chamada por Reel

    def test_foto_vira_so_foto(self):
        main.cfg_save({"hiker_key": "HK-KEY-1234"})
        HIKER_MEDIA_FOTO = {**HIKER_MEDIA, "video_url": None, "code": "Foto"}
        main.req = lambda *a, **k: HIKER_MEDIA_FOTO
        with self.assertRaises(RuntimeError) as cm:
            main.ig_video(main.cfg(), "https://www.instagram.com/p/Foto/", os.path.join(self.tmp.name, "x"), "max")
        self.assertTrue(any(s in str(cm.exception) for s in main.SO_FOTO))

    def test_salvos_pelo_login_e_erro_sem_prefixo_de_cookie(self):
        main.cfg_save({"ig_user": "@nan", "ig_pass": "SENHA-SECRETA", "col_instagram": "1"})
        with mock.patch.object(main, "ig_saved", lambda c: ["https://www.instagram.com/p/AbC/"]):
            self.assertEqual(main.col_instagram(main.cfg()), [("saved", ["https://www.instagram.com/p/AbC/"])])
        with mock.patch.object(main, "ig_saved", side_effect=RuntimeError(main.IG_ERR + "senha recusada. login SENHA-SECRETA")):
            main.collect_once()
        st = json.loads(main.q("SELECT v FROM kv WHERE k='col:instagram'")[0]["v"])
        self.assertTrue(st["erro"].startswith(main.IG_ERR))
        self.assertNotIn("Cookies expiraram", st["erro"])

    def test_login_refaz_quando_sessao_cai(self):
        main.cfg_save({"ig_user": "nan", "ig_pass": "SENHA-SECRETA"})
        LoginRequired = type("LoginRequired", (Exception,), {})
        clientes = []

        def cliente(c, novo=False):
            clientes.append(novo)
            return "cl-novo" if novo else "cl-velho"

        def fn(cl):
            if cl == "cl-velho":
                raise LoginRequired("sessão caiu")
            return "ok"
        with mock.patch.object(main, "ig_client", cliente):
            self.assertEqual(main.ig_call(main.cfg(), fn), "ok")
        self.assertEqual(clientes, [False, True])

    def test_codigo_fora_da_exportacao(self):
        main.cfg_save({"ig_codigo": "123456", "ig_pass": "x", "hiker_key": "y"})
        ex = main.exportar()
        self.assertNotIn("ig_codigo", ex)
        self.assertNotIn("ig_pass", ex)
        self.assertNotIn("hiker_key", ex)


class TestSbLocal(unittest.TestCase):
    def test_grava_e_le_na_pasta_local(self):
        import tempfile
        d = tempfile.mkdtemp()
        old = main.SB_LOCAL
        main.SB_LOCAL = d
        try:
            self.assertTrue(main.sb_ligado({"sb_on": "0"}))
            main.sb_put({}, "Reels/Um reel.md", "oi")
            self.assertEqual(main.sb_get({}, "Reels/Um reel.md"), b"oi")
            self.assertIsNone(main.sb_get({}, "Reels/nao.md"))
            with self.assertRaises(ValueError):
                main.sb_put({}, "../fora.md", "x")
        finally:
            main.SB_LOCAL = old
