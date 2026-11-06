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

# Thread de comparação real roda com PASSO_COMPARACAO=1 e sobreviveria ao teste; os testes não a exercitam.
PATCH_COMPARACAO = mock.patch.object(E, 'iniciar_comparacao')

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
            E.mudar({'cpu_pl1': 999}, False, 'x', 'CPU', E.descricao_cpu)
        self.assertEqual(E.ESTADO, antes)

    def test_remover_rtx_com_gpu_em_uso_pede_confirmacao_e_nao_persiste(self):
        res = E.mudar({'rtx': 'off'}, False, 'x', 'RTX', lambda est: est['rtx'])
        self.assertTrue(res.get('confirmar'))
        self.assertTrue(res['usos'])
        self.assertEqual(E.ESTADO['rtx'], 'on')

    def test_remover_rtx_confirmado_persiste(self):
        res = E.mudar({'rtx': 'off'}, True, 'x', 'RTX', lambda est: est['rtx'])
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

    def test_hora_sp_converte_utc_para_brasilia_com_virada_de_dia(self):
        import datetime
        instante = datetime.datetime(2024, 1, 1, 2, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(E.hora_sp('%d/%m %H:%M:%S', instante), '31/12 23:30:00')

    def test_cada_select_tem_id_unico_e_rotulo_proprio(self):
        import re
        html = E.pagina(E.status_data())
        ids = re.findall(r'<select id="([^"]+)"', html)
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)), ids)
        for ident in ids:
            with self.subTest(select=ident):
                self.assertRegex(html, r'<label class="campo" for="%s">[^<]+</label>' % ident)

    def test_todos_os_ids_da_pagina_sao_unicos(self):
        import re
        html = E.pagina(E.status_data())
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)), sorted({i for i in ids if ids.count(i) > 1}))

    def test_cada_controle_de_faixa_tem_rotulo_e_descricao(self):
        import re
        html = E.pagina(E.status_data())
        for ident in ('pl1', 'pl2'):
            with self.subTest(faixa=ident):
                self.assertIn('<label class="campo" for="%s">' % ident, html)
                self.assertRegex(html, r'id="%s"[^>]*aria-describedby="d-%s"' % (ident, ident))
                self.assertEqual(len(re.findall(r'id="d-%s"' % ident, html)), 1)


def _lum(hexa):
    canais = [int(hexa.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _razao(a, b):
    claro, escuro = sorted([_lum(a), _lum(b)], reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


NODE = shutil.which('node')

HARNESS_TEMA = r"""
const vm = require('vm');
const cfg = JSON.parse(process.argv[1]);
const attrs = {}, handlers = {}, store = Object.assign({}, cfg.store);
const storage = cfg.quebra
  ? {getItem() { throw new Error('bloqueado'); }, setItem() { throw new Error('bloqueado'); }}
  : {getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }};
const select = {value: '', listeners: {}, addEventListener(ev, fn) { this.listeners[ev] = fn; }};
const mm = {matches: cfg.escuro, addEventListener() {}};
const doc = {
  documentElement: {setAttribute(k, v) { attrs[k] = v; }},
  addEventListener(ev, fn) { handlers[ev] = fn; },
  getElementById(id) { return id === 'tema' ? select : null; },
};
vm.runInNewContext(cfg.script, {document: doc, localStorage: storage, window: {matchMedia: () => mm}});
const saida = {inicial: attrs['data-tema']};
handlers.DOMContentLoaded();
saida.selecionado = select.value;
if (cfg.escolha) {
  select.value = cfg.escolha;
  select.listeners.change();
  saida.apos_escolha = attrs['data-tema'];
  saida.salvo = store['economia-tema'] === undefined ? null : store['economia-tema'];
}
console.log(JSON.stringify(saida));
"""


class Tema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()
        carregar()

    def test_texto_sobre_fundo_atinge_aa_nos_dois_temas(self):
        pares = [('cor-texto', 'cor-fundo'), ('cor-texto', 'cor-superficie'), ('cor-texto-2', 'cor-superficie'),
                 ('cor-ajuda', 'cor-superficie'), ('cor-link', 'cor-superficie'), ('cor-link', 'cor-fundo'),
                 ('cor-primaria-texto', 'cor-primaria-fundo'), ('cor-secundaria-texto', 'cor-secundaria-fundo'),
                 ('cor-perigo-texto', 'cor-perigo-fundo'), ('cor-perfil-ativo-texto', 'cor-perfil-ativo-fundo'),
                 ('cor-texto', 'cor-pre-fundo'), ('cor-texto', 'cor-aviso-fundo'),
                 ('cor-texto', 'cor-ok-fundo'), ('cor-texto', 'cor-erro-fundo')]
        for tema, valores in E.TEMAS.items():
            for texto, fundo in pares:
                with self.subTest(tema=tema, texto=texto, fundo=fundo):
                    self.assertGreaterEqual(_razao(valores[texto], valores[fundo]), 4.5)

    def test_bordas_de_controle_atingem_3_para_1_nos_dois_temas(self):
        pares = [('cor-borda-ui', 'cor-superficie'), ('cor-borda-forte', 'cor-superficie'),
                 ('cor-perigo-fundo', 'cor-superficie'), ('cor-aviso-borda', 'cor-aviso-fundo'),
                 ('cor-ok-borda', 'cor-ok-fundo'), ('cor-erro-borda', 'cor-erro-fundo')]
        for tema, valores in E.TEMAS.items():
            for borda, fundo in pares:
                with self.subTest(tema=tema, borda=borda, fundo=fundo):
                    self.assertGreaterEqual(_razao(valores[borda], valores[fundo]), 3.0)

    def test_os_dois_temas_definem_as_mesmas_variaveis(self):
        self.assertEqual(set(E.TEMAS['claro']), set(E.TEMAS['escuro']))

    def test_seletor_de_tema_fica_na_linha_do_cabecalho_com_rotulo_e_tres_opcoes(self):
        import re
        html = E.pagina(E.status_data())
        cabecalho = re.search(r'<header>.*?</header>', html, re.S).group(0)
        self.assertLess(cabecalho.index('Ver estado em JSON'), cabecalho.index('<select id="tema"'))
        self.assertIn('<label class="campo" for="tema">Tema</label>', cabecalho)
        seletor = re.search(r'<select id="tema".*?</select>', cabecalho, re.S)
        opcoes = re.findall(r'<option value="(\w+)"', seletor.group(0))
        self.assertEqual(opcoes, ['claro', 'escuro', 'sistema'])
        self.assertNotIn('class="tema"', html)

    def test_script_do_tema_fica_no_head_antes_do_css(self):
        html = E.pagina(E.status_data())
        self.assertLess(html.index('<script>'), html.index('<style>'))
        self.assertLess(html.index('<style>'), html.index('<body>'))

    @unittest.skipUnless(NODE, 'node não instalado')
    def test_script_do_tema_resolve_preferencia_e_salva_escolha(self):
        import re
        casos = [
            ('sem preferencia, sistema escuro', {'store': {}, 'escuro': True},
             {'inicial': 'escuro', 'selecionado': 'sistema'}),
            ('sem preferencia, sistema claro', {'store': {}, 'escuro': False},
             {'inicial': 'claro', 'selecionado': 'sistema'}),
            ('preferencia claro vence sistema escuro', {'store': {'economia-tema': 'claro'}, 'escuro': True},
             {'inicial': 'claro', 'selecionado': 'claro'}),
            ('valor invalido cai em sistema', {'store': {'economia-tema': 'xyz'}, 'escuro': True},
             {'inicial': 'escuro', 'selecionado': 'sistema'}),
            ('escolha escuro sobre sistema claro grava', {'store': {}, 'escuro': False, 'escolha': 'escuro'},
             {'inicial': 'claro', 'selecionado': 'sistema', 'apos_escolha': 'escuro', 'salvo': 'escuro'}),
            ('storage bloqueado nao quebra', {'store': {}, 'quebra': True, 'escuro': True, 'escolha': 'claro'},
             {'inicial': 'escuro', 'selecionado': 'sistema', 'apos_escolha': 'claro', 'salvo': None}),
        ]
        script = re.search(r'<script>(.*?)</script>', E.pagina(E.status_data()), re.S).group(1)
        for nome, cfg, esperado in casos:
            with self.subTest(caso=nome):
                cfg = dict(cfg, script=script)
                r = subprocess.run([NODE, '-e', HARNESS_TEMA, json.dumps(cfg)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                saida = json.loads(r.stdout)
                for chave, valor in esperado.items():
                    self.assertEqual(saida.get(chave), valor, saida)



class Compose(unittest.TestCase):
    def test_compose_embutido_esta_sincronizado_com_o_codigo(self):
        r = subprocess.run([sys.executable, str(RAIZ / 'tools' / 'gerar_compose.py'), '--check'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


def setUpModule():
    PATCH_COMPARACAO.start()


def tearDownModule():
    PATCH_COMPARACAO.stop()
    shutil.rmtree(TMP, ignore_errors=True)


class ConferenciaLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()
        carregar()

    def setUp(self):
        carregar()
        E.ULTIMO_REAPLICADO.clear()

    def test_limite_w_dentro_da_tolerancia_e_ok(self):
        self.assertEqual(E.conferir_w('PL1 MSR', 35_000_000, 35_250_000)['status'], 'ok')

    def test_limite_w_fora_da_tolerancia_nao_pegou(self):
        e = E.conferir_w('PL1 MSR', 35_000_000, 40_000_000)
        self.assertEqual(e['status'], 'nao_pegou')
        self.assertTrue(E.linha_conferencia(e).startswith('NÃO PEGOU: PL1 MSR'))

    def test_cpu_todas_com_pedido_e_ok(self):
        e = E.conferir_por_cpu('EPP', 'power', [('cpu0', 'power'), ('cpu1', 'power')])
        self.assertEqual(e['status'], 'ok')

    def test_cpu_uma_divergente_nao_pegou_com_cpu_citada(self):
        e = E.conferir_por_cpu('EPP', 'power', [('cpu0', 'power'), ('cpu1', 'balance_power')])
        self.assertEqual(e['status'], 'nao_pegou')
        self.assertIn('cpu1=balance_power', e['lido'])

    def test_aspm_le_token_entre_colchetes(self):
        original = E.rd(E.ASPM)
        self.addCleanup(E.wr, E.ASPM, original)
        E.wr(E.ASPM, 'default [powersave] powersupersave')
        self.assertEqual(E.aspm_ativo(), 'powersave')

    def test_apply_deixa_hardware_conferido(self):
        E.apply()
        self.assertEqual([e for e in E.conferir_tudo() if e['status'] != 'ok'], [])

    def test_escrita_divergente_aparece_como_nao_pegou(self):
        E.apply()
        E.wr('/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference',
             'power' if E.ESTADO['epp'] != 'power' else 'balance_power')
        epp = [e for e in E.conferir_tudo() if e['controle'] == 'EPP'][0]
        self.assertEqual(epp['status'], 'nao_pegou')

    def test_ciclo_silencioso_quando_tudo_ok(self):
        E.apply()
        with mock.patch.object(E, 'log') as lg:
            E.ciclo_periodico()
        self.assertEqual([c for c in lg.call_args_list if 'reaplicado' in c.args[0]], [])

    def test_ciclo_reaplica_e_loga_divergencia_uma_vez(self):
        E.apply()
        E.wr('/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference',
             'power' if E.ESTADO['epp'] != 'power' else 'balance_power')
        with mock.patch.object(E, 'log') as lg:
            E.ciclo_periodico()
        linhas = [c.args[0] for c in lg.call_args_list if 'reaplicado' in c.args[0]]
        self.assertEqual(len(linhas), 1)
        self.assertIn('EPP', linhas[0])

    def test_ciclo_nao_repete_caso_que_continua_divergindo(self):
        E.apply()
        E.wr('/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference',
             'power' if E.ESTADO['epp'] != 'power' else 'balance_power')
        with mock.patch.object(E, 'apply'), mock.patch.object(E, 'log') as lg:
            E.ciclo_periodico()
            E.ciclo_periodico()
        linhas = [c.args[0] for c in lg.call_args_list if 'reaplicado' in c.args[0]]
        self.assertEqual(len(linhas), 1)

    def test_acao_expoe_resultado_em_status_json(self):
        with mock.patch.object(E, 'log') as lg:
            E.acao_perfil({'perfil': 'equilibrio'}, True)
        linhas = [c.args[0] for c in lg.call_args_list]
        self.assertTrue(any('ação: perfil ' in l and '→ Equilíbrio' in l for l in linhas))
        self.assertEqual(E.status_data()['ultima_acao']['resultado'], 'ok')

    def test_aviso_com_divergencia_usa_classe_erro_e_lista(self):
        falha = E.entrada('EPP', 'power', 'balance_power', 'nao_pegou')
        html = E.aviso_html({'tipo': 'ok', 'texto': 'Perfil aplicado.', 'conferencia': [falha]})
        self.assertIn('class="aviso erro"', html)
        self.assertIn('1 controle não pegou', html)
        self.assertIn('EPP: não pegou. Pedido power, lido balance_power.', html)


class GovernorEPP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        E.criar_simulacao()
        carregar()

    def setUp(self):
        carregar()
        E.ULTIMO_REAPLICADO.clear()
        E.mudar({'governor': 'powersave', 'epp': 'balance_performance'}, True, 'x', 'EPP', lambda est: est['epp'])

    def cpus(self):
        return E.gl('/sys/devices/system/cpu/cpu[0-9]*/cpufreq')

    def governor_hw(self, valor):
        for c in self.cpus():
            E.wr(c + '/scaling_governor', valor)

    def test_epp_travado_em_performance_e_liberado_trocando_governor(self):
        self.governor_hw('performance')
        epp0 = self.cpus()[0] + '/energy_performance_preference'
        self.assertFalse(E.wr(epp0, 'power'))  # simulação do EBUSY: EPP não muda com governor performance
        self.assertEqual(E.rd(epp0), 'balance_performance')
        E.mudar({'epp': 'power'}, True, 'x', 'EPP', lambda est: est['epp'])
        self.assertEqual(E.ESTADO['governor'], 'powersave')
        self.assertEqual({v for _, v in E.lidos_cpufreq('energy_performance_preference')}, {'power'})
        self.assertEqual({v for _, v in E.lidos_cpufreq('scaling_governor')}, {'powersave'})
        self.assertEqual([e for e in E.conferir_tudo() if e['status'] != 'ok'], [])
        self.assertEqual(E.FLASH['v']['notas'], [E.MSG_GOV_TROCADO])

    def test_governor_performance_manual_avisa_que_epp_fica_preso(self):
        E.mudar({'governor': 'performance'}, True, 'x', 'Governor', lambda est: est['governor'])
        self.assertEqual(E.ESTADO['governor'], 'performance')
        self.assertEqual(E.ESTADO['epp'], 'balance_performance')
        self.assertEqual(E.FLASH['v']['notas'], [E.MSG_EPP_PRESO])

    def test_epp_nao_pegou_com_governor_performance_explica_motivo(self):
        self.governor_hw('performance')
        E.ESTADO['epp'] = 'power'
        epp = [e for e in E.conferir_tudo() if e['controle'] == 'EPP'][0]
        self.assertEqual(epp['status'], 'nao_pegou')
        self.assertEqual(epp['motivo'], 'governor em performance prende o EPP')

    def test_apply_escreve_todos_os_governors_antes_dos_epps(self):
        ordem = []
        real = E.wr

        def gravar(p, v):
            if p.endswith(('scaling_governor', 'energy_performance_preference')):
                ordem.append(p.rsplit('/', 1)[1])
            return real(p, v)

        with mock.patch.object(E, 'wr', side_effect=gravar):
            E.apply()
        n = len(ordem) // 2
        self.assertGreater(n, 0)
        self.assertEqual(ordem, ['scaling_governor'] * n + ['energy_performance_preference'] * n)


if __name__ == '__main__':
    unittest.main()
