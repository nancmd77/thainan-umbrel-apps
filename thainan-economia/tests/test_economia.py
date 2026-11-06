"""Testes do painel thainan-economia. Rodar: python3 -m unittest discover -s thainan-economia/tests -v

O módulo é importado em modo simulado (ECONOMIA_SIMULADO=1), então nada toca o sysfs real.
"""
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RAIZ = pathlib.Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix='economia-teste-')
os.environ['ECONOMIA_SIMULADO'] = '1'
os.environ['ECONOMIA_SIM_DIR'] = os.path.join(TMP, 'sim')
os.environ['ECONOMIA_SIM_GPU_EM_USO'] = '1'
sys.path.insert(0, str(RAIZ / 'src'))
import economia as E  # noqa: E402

CTX = {
    'cpu': {'long': 45, 'short': 90},
    'epp': ['default', 'performance', 'balance_performance', 'balance_power', 'power'],
    'governor': ['powersave', 'performance'],
    'aspm': ['default', 'performance', 'powersave', 'powersupersave'],
    'fans': ['quiet', 'low-power', 'balanced', 'performance'],
}


def estado_completo(**mudancas):
    est = dict(E.PERFIS['fabrica'])
    est.update(mudancas)
    return est


def carregar():
    """carregar_estado() atualiza E.ESTADO e não retorna; devolve uma cópia dele. Usa LOCK como o app."""
    with E.LOCK:
        E.carregar_estado()
        return dict(E.ESTADO)


class ValidacaoWatts(unittest.TestCase):
    def test_aceita_inteiro_e_texto_numerico_dentro_da_faixa(self):
        self.assertEqual(E.validar_watts(7, 5, 45), 7)
        self.assertEqual(E.validar_watts('45', 5, 45), 45)

    def test_rejeita_fora_da_faixa(self):
        with self.assertRaises(E.ErroValidacao):
            E.validar_watts(4, 5, 45)
        with self.assertRaises(E.ErroValidacao):
            E.validar_watts(46, 5, 45)

    def test_rejeita_tipos_ambiguos_e_nao_finitos(self):
        for ruim in (True, 7.5, 'abc', '7.5', '-3', float('nan'), float('inf'), None):
            with self.subTest(valor=ruim), self.assertRaises(E.ErroValidacao):
                E.validar_watts(ruim, 5, 45)


class ValidacaoCampos(unittest.TestCase):
    def test_fabrica_sempre_aceita_na_cpu(self):
        self.assertEqual(E.validar_campo('cpu_pl1', 'fabrica', CTX), 'fabrica')
        self.assertEqual(E.validar_campo('cpu_pl2', 'fabrica', CTX), 'fabrica')

    def test_cpu_respeita_teto_de_fabrica_por_tipo(self):
        self.assertEqual(E.validar_campo('cpu_pl2', 90, CTX), 90)
        with self.assertRaises(E.ErroValidacao):
            E.validar_campo('cpu_pl1', 46, CTX)

    def test_opcoes_fechadas_rejeitam_valor_fora_da_lista(self):
        for campo, valor in (('turbo', 'talvez'), ('rtx', 'ligado'), ('epp', 'turbo'), ('governor', 'x'),
                             ('aspm', '../../etc'), ('fans', 'turbo')):
            with self.subTest(campo=campo), self.assertRaises(E.ErroValidacao):
                E.validar_campo(campo, valor, CTX)

    def test_fans_quiet_so_vale_se_plataforma_tem_alternativa(self):
        self.assertEqual(E.validar_campo('fans', 'quiet', CTX), 'quiet')
        sem = dict(CTX, fans=['balanced'])
        with self.assertRaises(E.ErroValidacao):
            E.validar_campo('fans', 'quiet', sem)
        self.assertEqual(E.validar_campo('fans', 'auto', sem), 'auto')

    def test_campo_desconhecido_e_rejeitado(self):
        with self.assertRaises(E.ErroValidacao):
            E.validar_campo('shell', 'rm -rf /', CTX)


class ResolucaoFans(unittest.TestCase):
    def test_quiet_cai_para_low_power_quando_quiet_nao_existe(self):
        self.assertEqual(E.resolver_fans('quiet', ['low-power', 'balanced']), 'low-power')

    def test_performance_prefere_balanced_performance_se_nao_houver_performance(self):
        self.assertEqual(E.resolver_fans('performance', ['balanced-performance']), 'balanced-performance')

    def test_nome_direto_so_resolve_se_existir(self):
        self.assertEqual(E.resolver_fans('balanced', ['balanced']), 'balanced')
        self.assertIsNone(E.resolver_fans('balanced', ['quiet']))


class Combinacao(unittest.TestCase):
    def test_rajada_menor_que_continuo_e_rejeitada(self):
        est = estado_completo(cpu_pl1=50, cpu_pl2=30)
        with self.assertRaises(E.ErroValidacao):
            E.validar_combinacao(est, CTX)

    def test_fabrica_resolve_para_teto_do_ctx_na_comparacao(self):
        est = estado_completo(cpu_pl1='fabrica', cpu_pl2=30)
        with self.assertRaises(E.ErroValidacao):
            E.validar_combinacao(est, CTX)

    def test_limites_iguais_sao_aceitos(self):
        E.validar_combinacao(estado_completo(cpu_pl1=7, cpu_pl2=7), CTX)


class Perfis(unittest.TestCase):
    def test_todos_os_perfis_passam_na_validacao_do_hardware_tipico(self):
        for chave, p in E.PERFIS.items():
            with self.subTest(perfil=chave):
                est = {c: E.validar_campo(c, p[c], CTX) for c in E.CAMPOS}
                E.validar_combinacao(est, CTX)

    def test_economia_usa_7_w_nos_dois_limites(self):
        self.assertEqual((E.PERFIS['economia']['cpu_pl1'], E.PERFIS['economia']['cpu_pl2']), (7, 7))

    def test_perfil_ativo_identifica_e_mudanca_vira_personalizado(self):
        est = {c: E.PERFIS['equilibrio'][c] for c in E.CAMPOS}
        self.assertEqual(E.perfil_ativo(est), 'equilibrio')
        self.assertEqual(E.nome_perfil(est), 'Equilíbrio')
        est['turbo'] = 'off'
        self.assertIsNone(E.perfil_ativo(est))
        self.assertEqual(E.nome_perfil(est), 'Personalizado')

    def test_nomes_de_perfil_sao_unicos(self):
        nomes = [p['nome'] for p in E.PERFIS.values()]
        self.assertEqual(len(nomes), len(set(nomes)))


class OrigemPost(unittest.TestCase):
    def test_fetch_metadata_cross_site_e_bloqueado(self):
        self.assertFalse(E.origem_valida({'Sec-Fetch-Site': 'cross-site', 'Host': '10.21.0.1:8536'}))
        self.assertFalse(E.origem_valida({'Sec-Fetch-Site': 'same-site', 'Host': '10.21.0.1:8536'}))

    def test_fetch_metadata_same_origin_passa(self):
        self.assertTrue(E.origem_valida({'Sec-Fetch-Site': 'same-origin', 'Host': '10.21.0.1:8536'}))

    def test_sem_fetch_metadata_confere_origin_com_host(self):
        host = '10.21.0.1:8536'
        self.assertTrue(E.origem_valida({'Origin': 'http://' + host, 'Host': host}))
        self.assertFalse(E.origem_valida({'Origin': 'http://evil.example', 'Host': host}))

    def test_sem_nenhum_cabecalho_de_origem_passa(self):
        self.assertTrue(E.origem_valida({'Host': '10.21.0.1:8536'}))


class EstadoEmDisco(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()

    def setUp(self):
        self.caminho = E.P(E.DADOS)
        if os.path.exists(self.caminho):
            os.remove(self.caminho)

    def test_ida_e_volta_preserva_estado(self):
        with E.LOCK:
            E.ESTADO.clear()
            E.ESTADO.update(estado_completo(cpu_pl1=35, cpu_pl2=55, turbo='on', fans='auto'))
            E.salvar_estado()
        self.assertEqual(carregar()['cpu_pl1'], 35)
        self.assertEqual(carregar()['cpu_pl2'], 55)
        self.assertFalse(any(n.endswith('.tmp') for n in os.listdir(os.path.dirname(self.caminho))))

    def test_json_ilegivel_volta_ao_padrao(self):
        os.makedirs(os.path.dirname(self.caminho), exist_ok=True)
        with open(self.caminho, 'w') as f:
            f.write('{quebrado')
        est = carregar()
        self.assertEqual(est['turbo'], E.estado_padrao()['turbo'])

    def test_valor_salvo_invalido_cai_so_naquele_campo(self):
        os.makedirs(os.path.dirname(self.caminho), exist_ok=True)
        with open(self.caminho, 'w') as f:
            json.dump({'turbo': 'talvez', 'epp': 'performance'}, f)
        est = carregar()
        self.assertEqual(est['turbo'], E.estado_padrao()['turbo'])
        self.assertEqual(est['epp'], 'performance')

    def test_padrao_de_ambiente_invalido_nao_entra_no_estado(self):
        with mock.patch.dict(os.environ, {'CPU_W': '999'}):
            est = carregar()
        self.assertEqual(est['cpu_pl1'], 'fabrica')
        self.assertEqual(est['cpu_pl2'], 'fabrica')

    def test_padrao_de_ambiente_nan_vira_fabrica(self):
        self.assertEqual(E.estado_padrao({'CPU_W': 'nan'})['cpu_pl1'], 'fabrica')
        self.assertEqual(E.estado_padrao({'CPU_W': 'inf'})['cpu_pl1'], 'fabrica')


class PainelSimulado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()
        carregar()

    def setUp(self):
        carregar()

    def test_rejeita_valor_invalido_sem_alterar_estado(self):
        antes = dict(E.ESTADO)
        with self.assertRaises(E.ErroValidacao):
            E.mudar({'cpu_pl1': 999}, False, 'x')
        self.assertEqual(E.ESTADO, antes)

    def test_remover_rtx_com_gpu_em_uso_pede_confirmacao_e_nao_persiste(self):
        res = E.mudar({'rtx': 'off'}, False, 'x')
        self.assertTrue(res.get('confirmar'))
        self.assertTrue(res['usos'])
        self.assertEqual(E.ESTADO['rtx'], 'on')

    def test_remover_rtx_confirmado_persiste(self):
        res = E.mudar({'rtx': 'off'}, True, 'x')
        self.assertEqual(res, {})
        self.assertEqual(E.ESTADO['rtx'], 'off')

    def test_perfil_aplica_todos_os_campos(self):
        E.acao_perfil({'perfil': 'equilibrio'}, True)
        self.assertEqual(E.perfil_ativo(E.ESTADO), 'equilibrio')

    def test_perfil_desconhecido_e_rejeitado(self):
        with self.assertRaises(E.ErroValidacao):
            E.acao_perfil({'perfil': 'turbo_total'}, False)


class Paginas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()
        carregar()

    def test_pagina_renderiza_com_todos_os_formularios(self):
        html = E.pagina(E.status_data())
        for acao in ('/acao/perfil', '/acao/cpu', '/acao/turbo', '/acao/rtx',
                     '/acao/fans', '/acao/epp', '/acao/governor', '/acao/aspm'):
            with self.subTest(acao=acao):
                self.assertIn('action="%s"' % acao, html)

    def test_formulario_nao_carrega_token_nem_campo_oculto_de_segredo(self):
        html = E.pagina(E.status_data())
        self.assertNotIn('name="csrf"', html)
        self.assertNotIn('type="password"', html)

    def test_bloco_monta_legenda_e_botao(self):
        html = E.bloco('/acao/cpu', 'Limites', '<p>x</p>', 'Aplicar')
        self.assertIn('action="/acao/cpu"', html)
        self.assertIn('<legend>Limites</legend>', html)
        self.assertIn('>Aplicar</button>', html)

    def test_confirmacao_de_rtx_preserva_campos_do_formulario(self):
        usos = [{'processo': 'ollama', 'container': 'x', 'pid': '123'}]
        html = E.pagina_confirmacao('/acao/rtx', {'valor': 'off', 'confirmar': '0'}, usos)
        self.assertIn('name="valor" value="off"', html)
        self.assertIn('ollama', html)


class Compose(unittest.TestCase):
    def test_compose_embutido_esta_sincronizado_com_o_codigo(self):
        r = subprocess.run([sys.executable, str(RAIZ / 'tools' / 'gerar_compose.py'), '--check'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
