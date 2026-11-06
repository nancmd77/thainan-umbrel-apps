"""Painel de energia do Nonve (Acer Nitro, RTX 2050) para o app Umbrel thainan-economia.

Processo único. Estado do usuário em /data/estado.json (volume do app); o loop de 5 min
reaplica o estado salvo. ECONOMIA_SIMULADO=1 cria uma árvore sysfs/proc falsa em
ECONOMIA_SIM_DIR e não toca no hardware real.

Este arquivo é a fonte; tools/gerar_compose.py embute o código no docker-compose.yml.
"""
import os, re, glob, time, threading, subprocess, html, http.server, socketserver, json, urllib.parse, math, shutil, datetime

SIM = os.environ.get('ECONOMIA_SIMULADO', '0').strip().lower() in ('1', 'on', 'true', 'sim')
SIM_DIR = (os.environ.get('ECONOMIA_SIM_DIR') or '/tmp/economia-simulado') if SIM else ''
PORTA = int(os.environ.get('ECONOMIA_PORTA', '8536'))
BIND = os.environ.get('BIND', '127.0.0.1' if SIM else '10.21.0.1')
NIC = os.environ.get('NIC', 'enp64s0')
DADOS = '/data/estado.json'
ZONES = ['/sys/class/powercap/intel-rapl:0', '/sys/class/powercap/intel-rapl-mmio:0']
PP = '/sys/firmware/acpi/platform_profile'
EPP_LISTA = '/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_available_preferences'
GOV_LISTA = '/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors'
ASPM = '/sys/module/pcie_aspm/parameters/policy'
NO_TURBO = '/sys/devices/system/cpu/intel_pstate/no_turbo'
GUID = '79772EC5-04B1-4BFD-843C-61E7F77B6CC9'
HM_PATS = ('/sys/bus/wmi/drivers/acer-wmi*/*/health_mode', '/sys/devices/platform/acer-wmi*/health_mode',
           '/sys/bus/wmi/devices/*/health_mode', '/sys/devices/platform/acer-wmi*/*/health_mode')
CPU_MIN_W = 5
FALLBACK_W = {'long': 45, 'short': 90}
ALIAS_FABRICA = ('', 'fabrica', 'fábrica', 'default', 'factory', 'auto')
ALIAS_DESLIGADO = ('off', '0', 'false', 'nao', 'não', 'no')
QUIET_ORDEM = ('quiet', 'low-power', 'cool')
DESEMPENHO_ORDEM = ('performance', 'balanced-performance')
CAMPOS = ('cpu_pl1', 'cpu_pl2', 'turbo', 'rtx', 'fans', 'epp', 'governor', 'aspm')
PERFIS = {
    'economia': {'nome': 'Economia',
                 'descricao': 'CPU 7 W, turbo desligado, RTX removida, fans silenciosos, EPP power, ASPM powersupersave',
                 'cpu_pl1': 7, 'cpu_pl2': 7, 'turbo': 'off', 'rtx': 'off', 'fans': 'quiet', 'epp': 'power',
                 'governor': 'powersave', 'aspm': 'powersupersave'},
    'equilibrio': {'nome': 'Equilíbrio',
                   'descricao': 'CPU 35 W contínuo e 55 W de rajada, turbo ligado, RTX ligada, fans automáticos, EPP balance_power',
                   'cpu_pl1': 35, 'cpu_pl2': 55, 'turbo': 'on', 'rtx': 'on', 'fans': 'auto', 'epp': 'balance_power',
                   'governor': 'powersave', 'aspm': 'powersave'},
    'desempenho': {'nome': 'Desempenho',
                   'descricao': 'CPU nos limites de fábrica, turbo ligado, RTX ligada, fans em desempenho, EPP performance, ASPM performance',
                   'cpu_pl1': 'fabrica', 'cpu_pl2': 'fabrica', 'turbo': 'on', 'rtx': 'on', 'fans': 'performance',
                   'epp': 'performance', 'governor': 'powersave', 'aspm': 'performance'},
    'fabrica': {'nome': 'Fábrica',
                'descricao': 'Padrão de fábrica: CPU nos limites do firmware, turbo e RTX ligados, fans automáticos, EPP power',
                'cpu_pl1': 'fabrica', 'cpu_pl2': 'fabrica', 'turbo': 'on', 'rtx': 'on', 'fans': 'auto', 'epp': 'power',
                'governor': 'powersave', 'aspm': 'powersupersave'},
}
ROT_FANS = {'quiet': 'Silencioso (quiet)', 'low-power': 'Economia (low-power)', 'balanced': 'Equilibrado',
            'balanced-performance': 'Equilibrado com desempenho', 'performance': 'Desempenho', 'cool': 'Frio (cool)'}
ROT_EPP = {'default': 'padrão', 'performance': 'desempenho', 'balance_performance': 'equilíbrio com desempenho',
           'balance_power': 'equilíbrio com economia', 'power': 'economia'}
ROT_GOV = {'powersave': 'powersave (a EPP decide a frequência)', 'performance': 'performance (frequência máxima fixa)'}
ROT_ASPM = {'default': 'padrão do kernel', 'performance': 'performance (sem economia no PCIe)', 'powersave': 'powersave',
            'powersupersave': 'powersupersave (economia máxima)'}
PASSO_COMPARACAO = 1 if SIM else 30

LOG = []
LOCK = threading.RLock()
ESTADO = {}
FLASH = {'v': None}
GEN = {'v': 0}
RESCAN = {'done': False}
FAN = {'estado': 'verificando...', 'alvo': None}
BAT = {'v': 'verificando...'}
ST = {'w': None}
USOS_SIM = [{'pid': 4242, 'processo': 'ollama', 'container': 'openbot', 'dispositivo': '/dev/nvidia0'}]


class ErroValidacao(ValueError):
    pass


FUSO_SP = datetime.timezone(datetime.timedelta(hours=-3))  # America/Sao_Paulo: sem horário de verão desde 2019; imagem alpine não tem tzdata


def hora_sp(fmt, instante=None):
    instante = instante or datetime.datetime.now(datetime.timezone.utc)
    return instante.astimezone(FUSO_SP).strftime(fmt)


def log(m):
    m = hora_sp('%H:%M:%S ') + m
    print(m, flush=True)
    LOG.append(m)
    del LOG[:-40]


# ---------------- acesso ao sistema (em simulação tudo fica sob SIM_DIR) ----------------
def P(p):
    return SIM_DIR + p if SIM else p


def rd(p, d='?'):
    try:
        with open(P(p)) as f:
            return f.read().strip()
    except Exception:
        return d


def rd_int(p, d=0):
    try:
        return int(rd(p, str(d)))
    except ValueError:
        return d


def escrever(p, txt):
    os.makedirs(os.path.dirname(P(p)), exist_ok=True)
    with open(P(p), 'w') as f:
        f.write(str(txt))


def wr(p, v):
    try:
        with open(P(p), 'w') as f:
            f.write(str(v))
        return True
    except Exception:
        return False


def ex(p):
    return os.path.exists(P(p))


def isdir(p):
    return os.path.isdir(P(p))


def gl(pat, recursivo=False):
    if not SIM:
        return glob.glob(pat, recursive=recursivo)
    return [x[len(SIM_DIR):] for x in glob.glob(P(pat), recursive=recursivo)]


def real(p):
    r = os.path.realpath(P(p))
    return r[len(SIM_DIR):] if SIM and r.startswith(SIM_DIR) else r


def sh(cmd, t=90):
    if SIM:
        return 127, 'simulado: comando não executado'
    try:
        r = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=t)
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except Exception as x:
        return 99, str(x)


def host_sh(cmd, t=10, limite=300):
    if SIM:
        return 127, 'simulado: comando não executado'
    try:
        r = subprocess.run(['nsenter', '-t', '1', '-m', 'sh', '-c', cmd], capture_output=True, text=True, timeout=t)
        return r.returncode, (r.stdout + r.stderr).strip()[-limite:]
    except Exception as x:
        return 99, str(x)


def remover_pci(d):
    """Remove o dispositivo do barramento. d é o caminho sysfs completo."""
    if SIM:
        shutil.rmtree(P(d), ignore_errors=True)
        return
    subprocess.run(['sh', '-c', 'echo 1 > "$1"', 'sh', d + '/remove'], timeout=90)


def simular_gpu(presente):
    for d, device in (('/sys/bus/pci/devices/0000:01:00.0', '0x25a2'), ('/sys/bus/pci/devices/0000:01:00.1', '0x2291')):
        if presente:
            escrever(d + '/vendor', '0x10de')
            escrever(d + '/device', device)
            escrever(d + '/power/control', 'on')
        else:
            shutil.rmtree(P(d), ignore_errors=True)


def sim_gpu_presente():
    return os.path.isdir(P('/sys/bus/pci/devices/0000:01:00.0'))


def criar_simulacao():
    shutil.rmtree(SIM_DIR, ignore_errors=True)
    for z in ZONES:
        escrever(z + '/enabled', 1)
        escrever(z + '/energy_uj', 0)
        escrever(z + '/max_energy_range_uj', 262143328850)
        for i, (nome, uw) in enumerate((('long_term', 45000000), ('short_term', 90000000))):
            escrever(z + '/constraint_%d_name' % i, nome)
            escrever(z + '/constraint_%d_power_limit_uw' % i, uw)
            escrever(z + '/constraint_%d_max_power_uw' % i, uw)
    for c in range(4):
        d = '/sys/devices/system/cpu/cpu%d/cpufreq' % c
        escrever(d + '/scaling_governor', 'powersave')
        escrever(d + '/scaling_available_governors', 'performance powersave')
        escrever(d + '/energy_performance_preference', 'balance_performance')
        escrever(d + '/energy_performance_available_preferences', 'default performance balance_performance balance_power power')
        escrever(d + '/scaling_cur_freq', 1200000)
    escrever(NO_TURBO, 0)
    escrever(ASPM, '[default] performance powersave powersupersave')
    escrever('/sys/module/acer_wmi/parameters/predator_v4', 'N')
    escrever(PP, 'balanced')
    escrever(PP + '_choices', 'low-power quiet balanced balanced-performance performance')
    escrever('/sys/class/hwmon/hwmon0/name', 'acer')
    escrever('/sys/class/hwmon/hwmon0/fan1_input', 2100)
    escrever('/sys/class/hwmon/hwmon0/fan2_input', 1900)
    escrever('/sys/class/hwmon/hwmon1/name', 'coretemp')
    escrever('/sys/class/hwmon/hwmon1/temp1_input', 61000)
    escrever('/sys/class/hwmon/hwmon2/name', 'acpitz')
    escrever('/sys/class/hwmon/hwmon2/temp1_input', 48000)
    nic_pci = '/sys/bus/pci/devices/0000:40:00.0'
    escrever(nic_pci + '/vendor', '0x10ec')
    for k in ('l1_aspm', 'l1_1_aspm', 'l1_2_aspm', 'l0s_aspm'):
        escrever(nic_pci + '/link/' + k, 1)
    escrever(nic_pci + '/power/control', 'auto')
    os.makedirs(P('/sys/class/net/' + NIC), exist_ok=True)
    os.symlink(P(nic_pci), P('/sys/class/net/%s/device' % NIC))
    simular_gpu(True)
    escrever('/sys/bus/pci/rescan', '')
    b = '/sys/class/power_supply/BAT1'
    escrever(b + '/status', 'Discharging')
    escrever(b + '/capacity', 78)
    escrever(b + '/current_now', 2100000)
    escrever(b + '/voltage_now', 12400000)
    escrever(b + '/charge_control_end_threshold', 100)
    escrever(b + '/charge_control_start_threshold', 95)
    escrever('/sys/class/power_supply/ACAD/online', 1)
    escrever('/sys/kernel/security/lockdown', 'none [integrity] confidentiality')
    escrever('/proc/modules', 'nvidia 9000000 0 - Live 0x0\n')
    escrever('/proc/driver/nvidia/version', 'NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.120  (simulado)')
    escrever('/etc/docker/daemon.json', json.dumps({'runtimes': {'nvidia': {'path': 'nvidia-container-runtime', 'args': []}}}))
    for n in ('nvidiactl', 'nvidia0', 'nvidia-uvm'):
        escrever('/dev/' + n, '')


def simular_energia():
    w = min(rd_int(ZONES[0] + '/constraint_0_power_limit_uw', 0) / 1e6, 30) * 0.6
    e = rd_int(ZONES[0] + '/energy_uj', 0)
    escrever(ZONES[0] + '/energy_uj', e + int(w * 15 * 1e6))


# ---------------- estado, validação e perfis (funções puras onde possível) ----------------
def estado_padrao(env=None):
    """Valores iniciais vindos do ambiente. Só valem até existir estado salvo."""
    env = os.environ if env is None else env
    cpu = (env.get('CPU_W') or 'fabrica').strip()
    if cpu.lower() in ALIAS_FABRICA:
        pl = 'fabrica'
    else:
        try:
            v = float(cpu)
            pl = int(v) if v == int(v) else v
        except (ValueError, OverflowError):
            pl = 'fabrica'
    tb = (env.get('TURBO') or 'on').strip().lower()
    rt = (env.get('RTX') or 'on').strip().lower()
    return {'cpu_pl1': pl, 'cpu_pl2': pl,
            'turbo': 'off' if tb in ALIAS_DESLIGADO else 'on',
            'rtx': 'off' if rt in ALIAS_DESLIGADO else 'on',
            'fans': 'auto', 'epp': 'power', 'governor': 'powersave', 'aspm': 'powersupersave'}


def validar_watts(valor, minimo, maximo):
    if isinstance(valor, bool):
        raise ErroValidacao('valor inválido')
    if isinstance(valor, str) and re.fullmatch(r'\s*\d{1,3}\s*', valor):
        num = int(valor)
    elif isinstance(valor, int):
        num = valor
    elif isinstance(valor, float) and math.isfinite(valor) and valor == int(valor):
        num = int(valor)
    else:
        raise ErroValidacao('valor inválido: use número inteiro de watts')
    maximo = max(maximo, minimo)
    if num < minimo or num > maximo:
        raise ErroValidacao('%d W fora da faixa segura (%d a %d W)' % (num, minimo, maximo))
    return num


def escolha(valor, opcoes):
    if isinstance(valor, str) and valor in opcoes:
        return valor
    raise ErroValidacao('opção inválida ou indisponível neste sistema')


def resolver_fans(pref, ch):
    """Traduz o pedido de fans para um perfil real de plataforma (None se não houver)."""
    if pref == 'quiet':
        return next((q for q in QUIET_ORDEM if q in ch), None)
    if pref == 'performance':
        return next((q for q in DESEMPENHO_ORDEM if q in ch), None)
    return pref if pref in ch else None


def validar_campo(nome, valor, ctx):
    if nome in ('cpu_pl1', 'cpu_pl2'):
        if valor == 'fabrica':
            return 'fabrica'
        kind = 'long' if nome == 'cpu_pl1' else 'short'
        return validar_watts(valor, CPU_MIN_W, ctx['cpu'][kind])
    if nome in ('turbo', 'rtx'):
        return escolha(valor, ('on', 'off'))
    if nome == 'fans':
        if valor == 'auto':
            return valor
        if isinstance(valor, str) and (valor in ctx['fans'] or (valor in ('quiet', 'performance') and resolver_fans(valor, ctx['fans']))):
            return valor
        raise ErroValidacao('perfil de fans indisponível neste notebook')
    if nome == 'epp':
        return escolha(valor, ctx['epp'])
    if nome == 'governor':
        return escolha(valor, ctx['governor'])
    if nome == 'aspm':
        return escolha(valor, ctx['aspm'])
    raise ErroValidacao('campo desconhecido: %s' % nome)


def pares_cpu(est, ctx):
    pl1 = ctx['cpu']['long'] if est['cpu_pl1'] == 'fabrica' else est['cpu_pl1']
    pl2 = ctx['cpu']['short'] if est['cpu_pl2'] == 'fabrica' else est['cpu_pl2']
    return pl1, pl2


def validar_combinacao(est, ctx):
    pl1, pl2 = pares_cpu(est, ctx)
    if pl2 < pl1:
        raise ErroValidacao('a rajada (PL2) não pode ser menor que o limite contínuo (PL1)')


def perfil_ativo(est):
    for chave, p in PERFIS.items():
        if all(est.get(c) == p[c] for c in CAMPOS):
            return chave
    return None


def nome_perfil(est):
    ativo = perfil_ativo(est)
    return PERFIS[ativo]['nome'] if ativo else 'Personalizado'


def tipo_limite(nome, i):
    return 'short' if ('short' in nome.lower() or i == 1) else 'long'


def descricao_cpu(est):
    if est['cpu_pl1'] == 'fabrica' and est['cpu_pl2'] == 'fabrica':
        return 'fábrica'
    return 'PL1 %s W / PL2 %s W' % (est['cpu_pl1'] if est['cpu_pl1'] != 'fabrica' else 'fábrica',
                                    est['cpu_pl2'] if est['cpu_pl2'] != 'fabrica' else 'fábrica')


def opcoes_lista(p):
    return rd(p, '').split()


def opcoes_aspm():
    return [t.strip('[]') for t in rd(ASPM, '').split()]


def opcoes_fans():
    return rd(PP + '_choices', '').split() if ex(PP) else []


def limites_cpu():
    """Teto de fábrica por tipo de limite: menor max_power entre zonas; fallback se ausente."""
    vals = {'long': [], 'short': []}
    for z in ZONES:
        for i in (0, 1):
            n = rd(z + '/constraint_%d_name' % i, '')
            if not n:
                continue
            m = rd_int(z + '/constraint_%d_max_power_uw' % i, 0)
            vals[tipo_limite(n, i)].append(m / 1e6 if m > 0 else FALLBACK_W[tipo_limite(n, i)])
    return {k: max(CPU_MIN_W, int(min(v))) if v else FALLBACK_W[k] for k, v in vals.items()}


def contexto_hw():
    return {'cpu': limites_cpu(), 'epp': opcoes_lista(EPP_LISTA), 'governor': opcoes_lista(GOV_LISTA),
            'aspm': opcoes_aspm(), 'fans': opcoes_fans()}


def carregar_estado():
    base = estado_padrao()
    try:
        with open(P(DADOS)) as f:
            bruto = json.load(f)
        if not isinstance(bruto, dict):
            raise ValueError('formato inesperado')
    except FileNotFoundError:
        bruto = {}
    except Exception as x:
        log('estado salvo ilegível, usando padrão: %s' % x)
        bruto = {}
    ctx = contexto_hw()
    est = dict(base)
    for k in CAMPOS:
        if k in bruto:
            try:
                est[k] = validar_campo(k, bruto[k], ctx)
                continue
            except ErroValidacao as x:
                log('estado salvo %s descartado (%s); usando padrão' % (k, x))
        try:
            est[k] = validar_campo(k, est[k], ctx)
        except ErroValidacao as x:
            log('padrão de %s inválido neste sistema (%s); usando fábrica' % (k, x))
            est[k] = validar_campo(k, PERFIS['fabrica'][k], ctx)
    try:
        validar_combinacao(est, ctx)
    except ErroValidacao as x:
        log('estado salvo inconsistente (%s); CPU volta ao padrão' % x)
        est['cpu_pl1'], est['cpu_pl2'] = 'fabrica', 'fabrica'
    ESTADO.clear()
    ESTADO.update(est)
    log('estado: perfil %s, %s' % (nome_perfil(ESTADO), json.dumps(ESTADO, ensure_ascii=False)))


def salvar_estado():
    caminho = P(DADOS)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(ESTADO, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


# ---------------- aplicação no hardware ----------------
def alvo_limite(z, i, kind):
    v = ESTADO['cpu_pl1' if kind == 'long' else 'cpu_pl2']
    if v != 'fabrica':
        return int(v * 1e6), 'ajustado (%g W)' % v
    m = rd_int(z + '/constraint_%d_max_power_uw' % i, 0)
    if m > 0:
        return m, 'fábrica (max_power)'
    return FALLBACK_W[kind] * 1000000, 'fábrica (fallback %dW)' % FALLBACK_W[kind]


def aplicar_cpu():
    for z in ZONES:
        if not isdir(z):
            continue
        wr(z + '/enabled', 1)
        for i in (0, 1):
            n = rd(z + '/constraint_%d_name' % i, '')
            if not n:
                continue
            kind = tipo_limite(n, i)
            val, src = alvo_limite(z, i, kind)
            ok = wr(z + '/constraint_%d_power_limit_uw' % i, val)
            log('limite CPU %s c%d (%s): %.1f W [%s]%s' % (os.path.basename(z), i, n, val / 1e6, src, '' if ok else ' (falhou)'))


def resolver_e_aplicar_fans():
    if not ex(PP):
        return
    ch = opcoes_fans()
    if ESTADO['fans'] == 'auto':
        alvo = FAN.get('alvo') or next((q for q in QUIET_ORDEM if q in ch), None)
    else:
        alvo = resolver_fans(ESTADO['fans'], ch)
        if not alvo:
            log('perfil de fans %s indisponível (opções: %s)' % (ESTADO['fans'], ' '.join(ch)))
            return
    if alvo and rd(PP) != alvo:
        ok = wr(PP, alvo)
        log('perfil ' + alvo + (' aplicado' if ok else ' falhou') + ' (opções: ' + ' '.join(ch) + ')')


def rpm():
    tot = 0
    for h in gl('/sys/class/hwmon/hwmon*'):
        for f in gl(h + '/fan*_input'):
            tot += rd_int(f, 0)
    return tot


def comparar_perfis(gen):
    ch = opcoes_fans()
    cand = [q for q in ('low-power', 'quiet') if q in ch]
    if len(cand) < 2:
        return
    res = {}
    for q in cand:
        with LOCK:
            if GEN['v'] != gen:
                return
            FAN['alvo'] = q
            resolver_e_aplicar_fans()
        lido = []
        for _ in range(6):
            time.sleep(PASSO_COMPARACAO)
            if GEN['v'] != gen:
                return
            lido.append(rpm())
        res[q] = sum(lido[2:]) / len(lido[2:])
    with LOCK:
        if GEN['v'] != gen:
            return
        FAN['alvo'] = min(cand, key=lambda q: (res[q], q != 'quiet'))
        resolver_e_aplicar_fans()
    log('comparação de perfis (RPM, média dos últimos 2 min): ' + ', '.join('%s %.0f' % (q, res[q]) for q in cand) + ' -> ' + FAN['alvo'])


def iniciar_comparacao():
    if ESTADO.get('fans') != 'auto' or not ex(PP):
        return
    GEN['v'] += 1
    threading.Thread(target=comparar_perfis, args=(GEN['v'],), daemon=True).start()


def nomes_containers():
    rc, out = host_sh("docker ps --no-trunc --format '{{.ID}} {{.Names}}'", t=5, limite=8000)
    nomes = {}
    if rc == 0:
        for linha in out.splitlines():
            partes = linha.split(None, 1)
            if len(partes) == 2 and re.fullmatch(r'[0-9a-f]{64}', partes[0]):
                nomes[partes[0][:12]] = partes[1]
    return nomes


def gpu_users():
    """Processos com /dev/nvidia* aberto, com o container de origem quando houver."""
    if SIM:
        return list(USOS_SIM) if os.environ.get('ECONOMIA_SIM_GPU_EM_USO', '1') == '1' and sim_gpu_presente() else []
    usos = []
    for pdir in glob.glob('/proc/[0-9]*'):
        pid = os.path.basename(pdir)
        if not pid.isdigit():
            continue
        dispositivo = None
        try:
            for fd in os.listdir(pdir + '/fd'):
                try:
                    alvo = os.readlink(pdir + '/fd/' + fd)
                except OSError:
                    continue
                if alvo.startswith('/dev/nvidia'):
                    dispositivo = alvo
                    break
        except OSError:
            continue
        if not dispositivo:
            continue
        m = re.search(r'([0-9a-f]{64})', rd(pdir + '/cgroup', ''))
        usos.append({'pid': int(pid), 'processo': rd(pdir + '/comm', '?'),
                     'container': m.group(1)[:12] if m else 'host', 'dispositivo': dispositivo})
    if any(u['container'] != 'host' for u in usos):
        nomes = nomes_containers()
        for u in usos:
            u['container'] = nomes.get(u['container'], u['container'])
    return usos


def nvidia_devs():
    return sorted(os.path.basename(d) for d in gl('/sys/bus/pci/devices/*')
                  if d.endswith('01:00.0') or d.endswith('01:00.1') or rd(d + '/vendor') == '0x10de')


def rescan():
    wr('/sys/bus/pci/rescan', 1)
    RESCAN['done'] = True
    if SIM:
        simular_gpu(True)
    time.sleep(2)
    log('após rescan PCI: ' + (', '.join(nvidia_devs()) or 'RTX ainda ausente'))


def aplicar_rtx(forcar):
    devs = nvidia_devs()
    if ESTADO['rtx'] == 'on':
        if devs:
            log('RTX mantida no barramento (RTX=on): ' + ', '.join(devs))
        elif not RESCAN['done']:
            log('RTX (01:00.x) ausente do barramento; acionando /sys/bus/pci/rescan uma vez...')
            rescan()
        else:
            log('RTX ausente do barramento (rescan PCI já executado)')
        return
    if not devs:
        return
    usos = gpu_users()
    if usos and not forcar:
        log('RTX em uso (%s): remoção adiada até confirmação no painel' % ', '.join(u['processo'] for u in usos))
        return
    for a in sorted(devs, key=lambda a: a.endswith('.0')):
        d = '/sys/bus/pci/devices/' + a
        parent = os.path.dirname(real(d))
        wr(d + '/power/control', 'auto')
        try:
            remover_pci(d)
        except Exception as x:
            log('remover %s travou: %s' % (a, x))
        ok = not ex(d)
        log('NVIDIA ' + a + (' removida do barramento' if ok else ' ainda presente'))
        if ok:
            wr(parent + '/power/control', 'auto')


def bat_limit():
    b = '/sys/class/power_supply/BAT1'
    e_ = b + '/charge_control_end_threshold'
    if ex(e_):
        st = b + '/charge_control_start_threshold'
        if ex(st):
            wr(st, 75)
        wr(e_, 80)
        return 'travado em ' + rd(e_) + '% (charge_control_end_threshold' + (', recomeça em ' + rd(st) + '%' if ex(st) else '') + ')'
    hm = [h for pat in HM_PATS for h in gl(pat)]
    if not hm:
        mods = gl('/lib/modules/' + os.uname().release + '/**/acer*battery*.ko*', recursivo=True)
        if mods:
            nome = os.path.basename(mods[0]).split('.ko')[0]
            if not SIM:
                try:
                    subprocess.run(['modprobe', '-q', nome], timeout=30)
                except Exception:
                    pass
            hm = [h for pat in HM_PATS for h in gl(pat)]
    if hm:
        wr(hm[0], 1)
        return 'battery health ' + ('ligado (para em 80%)' if rd(hm[0]) == '1' else 'falhou: ' + rd(hm[0])) + ' via ' + hm[0]
    return 'não suportado por este kernel (sem charge_control_end_threshold nem health_mode)'


def apply(forcar=False):
    with LOCK:
        resolver_e_aplicar_fans()
        aplicar_cpu()
        no_turbo = 1 if ESTADO['turbo'] == 'off' else 0
        ok = wr(NO_TURBO, no_turbo)
        log('turbo %s (no_turbo=%d)%s' % ('ligado' if no_turbo == 0 else 'desligado', no_turbo, '' if ok else ' (falhou)'))
        for c in gl('/sys/devices/system/cpu/cpu[0-9]*/cpufreq'):
            wr(c + '/scaling_governor', ESTADO['governor'])
            wr(c + '/energy_performance_preference', ESTADO['epp'])
        wr(ASPM, ESTADO['aspm'])
        nic = real('/sys/class/net/' + NIC + '/device')
        for k in ('l1_aspm', 'l1_1_aspm', 'l1_2_aspm', 'l0s_aspm'):
            wr(nic + '/link/' + k, 0)
        wr(nic + '/power/control', 'on')
        aplicar_rtx(forcar)
        BAT['v'] = bat_limit()


def mudar(campos, confirmar, msg):
    """Valida, persiste e aplica uma mudança. Retorna {'confirmar': True, 'usos': [...]} se precisa de confirmação."""
    with LOCK:
        ctx = contexto_hw()
        novo = dict(ESTADO)
        for k, v in campos.items():
            novo[k] = validar_campo(k, v, ctx)
        validar_combinacao(novo, ctx)
        if novo['rtx'] == 'off' and nvidia_devs():
            usos = gpu_users()
            if usos and not confirmar:
                return {'confirmar': True, 'usos': usos}
        ESTADO.clear()
        ESTADO.update(novo)
        salvar_estado()
        if 'fans' in campos:
            GEN['v'] += 1
            FAN['alvo'] = None
        if campos.get('rtx') == 'on':
            RESCAN['done'] = False
        apply(forcar=confirmar)
        if 'fans' in campos:
            iniciar_comparacao()
        FLASH['v'] = ('ok', msg)
    return {}


def meter():
    e0, mx = energia()
    t0 = time.time()
    while True:
        time.sleep(15)
        if SIM:
            simular_energia()
        e1, mx = energia()
        t1 = time.time()
        if e0 is not None and e1 is not None:
            d = e1 - e0
            if d < 0 and mx:
                d += mx
            ST['w'] = d / 1e6 / (t1 - t0)
        e0, t0 = e1, t1


def energia():
    try:
        return int(rd(ZONES[0] + '/energy_uj')), int(rd(ZONES[0] + '/max_energy_range_uj', '0'))
    except Exception:
        return None, None


# ---------------- diagnóstico ----------------
def lim(z):
    if not isdir(z):
        return 'não existe'
    out = []
    for i in (0, 1):
        n = rd(z + '/constraint_%d_name' % i, '')
        if n:
            out.append('%s %.1f W' % (n, rd_int(z + '/constraint_%d_power_limit_uw' % i, 0) / 1e6))
    return ', '.join(out) + ' (ativo: ' + rd(z + '/enabled') + ')'


def acer_fans():
    out = []
    for h in gl('/sys/class/hwmon/hwmon*'):
        for f in sorted(gl(h + '/fan*_input')):
            out.append('%s %d RPM' % (os.path.basename(f).split('_')[0], rd_int(f, 0)))
    return out


def temps():
    out = []
    for h in gl('/sys/class/hwmon/hwmon*'):
        nm = rd(h + '/name', '?')
        for t in sorted(gl(h + '/temp*_input')):
            out.append('%s/%s %.0f°C' % (nm, os.path.basename(t).split('_')[0], rd_int(t, 0) / 1000))
    return out


def achar_bin(nome):
    rc, out = host_sh('command -v ' + nome, t=5)
    if rc == 0 and out:
        return 'presente (%s)' % out
    if rc not in (99, 127):
        return 'não encontrado no PATH do host'
    rc2, out2 = sh('command -v ' + nome, t=5)
    if rc2 == 0 and out2:
        return 'presente no container (%s)' % out2
    return 'não verificado (sem acesso nsenter/pid host)'


def status_docker_nvidia():
    rc, out = host_sh('cat /etc/docker/daemon.json', t=5, limite=4000)
    if rc == 0 and out:
        try:
            dj = json.loads(out)
        except ValueError:
            tem = 'nvidia' in out
            return 'presente (runtime nvidia: %s)' % ('detectado' if tem else 'não detectado'), tem
        runtimes = dj.get('runtimes', {}) if isinstance(dj, dict) else {}
        tem = 'nvidia' in runtimes or (isinstance(dj, dict) and dj.get('default-runtime') == 'nvidia')
        return 'presente (runtime nvidia: %s)' % ('configurado' if tem else 'não configurado'), tem
    if rc != 99 and 'no such file' in out.lower():
        return '/etc/docker/daemon.json inexistente no host', False
    if ex('/etc/docker/daemon.json'):
        return 'presente no container', 'nvidia' in rd('/etc/docker/daemon.json', '')
    return 'não acessível (sem nsenter/pid host)', False


def probe_gpu():
    pci = []
    for d in sorted(gl('/sys/bus/pci/devices/*01:00.*')):
        driver = os.path.basename(real(d + '/driver')) if ex(d + '/driver') else 'nenhum'
        pci.append({'slot': os.path.basename(d), 'vendor': rd(d + '/vendor', ''),
                    'device': rd(d + '/device', ''), 'driver': driver})
    cmd = 'lspci -nn -s 01:00 2>/dev/null || lspci -s 01:00 2>/dev/null'
    rc, out = host_sh(cmd, t=5)
    if rc != 0 or not out:
        rc, out = sh(cmd, t=5)
    if rc == 0 and out:
        lspci = out
    elif pci:
        lspci = '; '.join('%s %s:%s driver=%s' % (p['slot'], p['vendor'], p['device'], p['driver']) for p in pci)
    else:
        lspci = 'nenhum dispositivo 01:00.x encontrado'
    nv_mod = [ln.split()[0] for ln in rd('/proc/modules', '').splitlines() if ln.split() and ln.split()[0].startswith('nvidia')]
    versao = rd('/proc/driver/nvidia/version', '')
    daemon, tem_runtime = status_docker_nvidia()
    return {'pci': pci, 'lspci': lspci, 'lsmod_nvidia': nv_mod or 'nenhum',
            'dev_nvidia': sorted(gl('/dev/nvidia*')) or 'nenhum',
            'driver_version': versao.splitlines()[0] if versao else 'não encontrado',
            'nvidia_smi': achar_bin('nvidia-smi'), 'nvidia_container_runtime': achar_bin('nvidia-container-runtime'),
            'docker_daemon_json': daemon, 'docker_tem_runtime_nvidia': tem_runtime}


def probe():
    sb = gl('/sys/firmware/efi/efivars/SecureBoot-*')
    try:
        sbv = str(open(P(sb[0]), 'rb').read()[-1]) if sb else 'sem efivar'
    except Exception as x:
        sbv = str(x)
    r = os.uname().release
    log('diag: kernel ' + r + ' | lockdown ' + rd('/sys/kernel/security/lockdown') + ' | secureboot ' + sbv +
        ' | /lib/modules ' + ('sim' if os.path.exists('/lib/modules/' + r) else 'não') +
        ' | headers build ' + ('sim' if os.path.exists('/lib/modules/' + r + '/build') else 'não') +
        ' | WMI battery GUID ' + ('sim' if isdir('/sys/bus/wmi/devices/' + GUID) else 'não') +
        ' | acer-wmi params ' + (','.join(sorted(os.listdir(P('/sys/module/acer_wmi/parameters')))) if isdir('/sys/module/acer_wmi/parameters') else 'sem módulo'))
    log('platform_profile: %s (opções: %s)' % (rd(PP), rd(PP + '_choices')))
    log('acer_wmi predator_v4=%s' % rd('/sys/module/acer_wmi/parameters/predator_v4'))
    log('ASPM policy: ' + rd(ASPM))
    log('GPU: ' + probe_gpu()['lspci'])


def fans_utilizaveis():
    return ex(PP) or bool(acer_fans())


def setup_fans():
    log('fans antes: predator_v4=' + rd('/sys/module/acer_wmi/parameters/predator_v4') + ' | platform_profile ' +
        ('sim' if ex(PP) else 'não') + ' | fans ' + (', '.join(acer_fans()) or 'nenhuma'))
    if ex(PP):
        FAN['estado'] = 'platform_profile já existe (predator_v4=' + rd('/sys/module/acer_wmi/parameters/predator_v4') + ')'
        iniciar_comparacao()
        return
    if rd('/sys/module/acer_wmi/parameters/predator_v4') == 'Y':
        FAN['estado'] = 'predator_v4 já ligado e sem platform_profile'
        return
    rc, o = sh('command -v kmod >/dev/null || apk add --no-cache kmod', 180)
    log('kmod: ' + ('ok' if rc == 0 else 'falhou ' + o))
    rc, o = sh('modprobe -r acer_wmi && modprobe acer_wmi predator_v4=1')
    log('reload acer_wmi predator_v4=1: rc=%d %s' % (rc, o))
    time.sleep(5)
    if not isdir('/sys/module/acer_wmi'):
        rc2, o2 = sh('modprobe acer_wmi')
        log('acer_wmi fora; recarregado sem parâmetro: rc=%d %s' % (rc2, o2))
        FAN['estado'] = 'reload com predator_v4=1 falhou; módulo recarregado como antes'
        return
    log('fans depois: predator_v4=' + rd('/sys/module/acer_wmi/parameters/predator_v4') + ' | platform_profile ' +
        ('sim: ' + rd(PP + '_choices') if ex(PP) else 'não') + ' | fans ' + (', '.join(acer_fans()) or 'nenhuma'))
    if fans_utilizaveis():
        FAN['estado'] = 'acer_wmi com predator_v4=1'
        if ex(PP):
            iniciar_comparacao()
    else:
        rc3, o3 = sh('modprobe -r acer_wmi && modprobe acer_wmi')
        if not isdir('/sys/module/acer_wmi'):
            sh('modprobe acer_wmi')
        log('nada útil com predator_v4=1; voltou ao normal: rc=%d predator_v4=%s %s' % (rc3, rd('/sys/module/acer_wmi/parameters/predator_v4'), o3))
        FAN['estado'] = 'sem controle de fans por software (predator_v4=1 não expôs platform_profile nem fans)'


def aspm_texto():
    return rd(ASPM, '?')


def status_data():
    b = '/sys/class/power_supply/BAT1'
    bw = None
    if ex(b + '/current_now'):
        bw = rd_int(b + '/current_now') * rd_int(b + '/voltage_now') / 1e12
    nv = nvidia_devs()
    usos = gpu_users() if nv else []
    if ESTADO['rtx'] == 'off':
        rtx_status = 'removida do barramento' if not nv else (
            'presente; remoção pendente (em uso por %d processo(s))' % len(usos) if usos else 'presente; remoção pendente')
    else:
        rtx_status = ('presente: ' + ', '.join(nv)) if nv else 'ausente (rescan executado)'
    ch = opcoes_fans()
    lim_ = limites_cpu()
    return {
        'perfil_ativo': nome_perfil(ESTADO),
        'cpu_potencia_w': ST['w'],
        'cpu_w_config': descricao_cpu(ESTADO),
        'turbo_config': ESTADO['turbo'],
        'rtx_config': ESTADO['rtx'],
        'limite_msr': lim(ZONES[0]),
        'limite_mmio': lim(ZONES[1]),
        'turbo': 'desligado' if rd(NO_TURBO) == '1' else 'ligado',
        'epp_governor': rd('/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference', '?') + ' / ' +
                        rd('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor', '?'),
        'freq_cpu0_mhz': rd_int('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', 0) // 1000,
        'aspm': aspm_texto(),
        'rtx_status': rtx_status,
        'bateria': rd(b + '/status', '?') + ' ' + rd(b + '/capacity', '?') + '%',
        'tomada': 'conectada' if rd('/sys/class/power_supply/ACAD/online', '0') == '1' else 'desconectada',
        'potencia_bateria_w': None if bw is None else round(bw, 2),
        'limite_bateria': BAT['v'],
        'platform_profile': rd(PP, '?') + ' (opções: ' + ' '.join(ch) + ')',
        'fans': ', '.join(acer_fans()) or 'RPM não exposto',
        'acer_wmi': FAN['estado'] + ' | predator_v4=' + rd('/sys/module/acer_wmi/parameters/predator_v4', '?'),
        'temperaturas': ', '.join(temps()) or 'nenhuma',
        'atualizado': hora_sp('%d/%m %H:%M:%S'),
        'gpu': probe_gpu(),
        'gpu_em_uso_por': usos,
        'controles': dict(ESTADO),
        'opcoes': {'epp': opcoes_lista(EPP_LISTA), 'governor': opcoes_lista(GOV_LISTA),
                   'aspm': opcoes_aspm(), 'fans': ch},
        'limites': lim_,
        'simulado': SIM,
    }


# ---------------- ações do painel ----------------
def acao_perfil(f, confirmar):
    nome = f.get('perfil', '')
    if nome not in PERFIS:
        raise ErroValidacao('perfil desconhecido')
    p = PERFIS[nome]
    campos = {c: p[c] for c in CAMPOS}
    extra = ''
    if p['fans'] != 'auto' and resolver_fans(p['fans'], opcoes_fans()) is None:
        campos['fans'] = 'auto'
        extra = ' Fans: nenhum perfil %s neste notebook; mantido automático.' % p['fans']
    return mudar(campos, confirmar, 'Perfil %s aplicado.%s' % (p['nome'], extra))


def acao_cpu(f, confirmar):
    return mudar({'cpu_pl1': f.get('pl1', ''), 'cpu_pl2': f.get('pl2', '')}, confirmar, 'Limites da CPU aplicados.')


def acao_unico(campo, titulo):
    def acao(f, confirmar):
        return mudar({campo: f.get('valor', '')}, confirmar, '%s atualizado.' % titulo)
    return acao


ROTAS_POST = {
    '/acao/perfil': acao_perfil,
    '/acao/cpu': acao_cpu,
    '/acao/turbo': acao_unico('turbo', 'Turbo'),
    '/acao/rtx': acao_unico('rtx', 'RTX'),
    '/acao/fans': acao_unico('fans', 'Perfil de fans'),
    '/acao/epp': acao_unico('epp', 'EPP'),
    '/acao/governor': acao_unico('governor', 'Governor'),
    '/acao/aspm': acao_unico('aspm', 'ASPM'),
}


# ---------------- páginas ----------------
CSS = """
*{box-sizing:border-box;border-radius:0}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f4f4f5;color:#18181b;font-size:16px;line-height:1.5}
main{max-width:920px;margin:0 auto;padding:16px}
h1{font-size:28px;margin:8px 0 4px}
h2{font-size:20px;margin:0 0 12px}
header p{margin:0 0 8px}
section{background:#fff;border:1px solid #d4d4d8;padding:16px;margin:0 0 16px}
table{width:100%;border-collapse:collapse;font-size:16px}
th,td{padding:8px;border-bottom:1px solid #e4e4e7;vertical-align:top;text-align:left;overflow-wrap:anywhere}
th[scope=row]{width:36%;font-weight:600;color:#3f3f46}
.perfis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.perfis form{margin:0}
.perfil{display:block;width:100%;min-height:44px;text-align:left;padding:12px;border:2px solid #18181b;background:#fff;color:#18181b;font:inherit;cursor:pointer}
.perfil span{display:block}
.perfil .titulo{font-size:18px;font-weight:700;margin-bottom:4px}
.perfil[aria-current=true]{background:#18181b;color:#fff}
.ctl{margin:0 0 16px}
fieldset{border:1px solid #d4d4d8;margin:0;padding:12px}
legend{font-weight:600;font-size:18px;padding:0 4px}
button{min-height:44px;padding:8px 16px;border:2px solid #18181b;background:#18181b;color:#fff;font:inherit;font-size:16px;cursor:pointer;margin-top:8px}
button.secundario{background:#fff;color:#18181b}
button.perigo{background:#b91c1c;border-color:#b91c1c}
label.op{display:inline-block;min-height:44px;padding:10px 16px 10px 0}
input[type=range]{width:100%;min-height:44px;margin:4px 0}
select{min-height:44px;font:inherit;padding:6px;width:100%;max-width:420px}
.campo{display:block;margin:12px 0 4px}
.ajuda{font-size:15px;color:#52525b;margin:4px 0 12px}
.atual{margin:0 0 8px}
.aviso{border-left:4px solid #b45309;background:#fffbeb;padding:12px;margin:0 0 16px}
.aviso.ok{border-left-color:#15803d;background:#f0fdf4}
.aviso.erro{border-left-color:#b91c1c;background:#fef2f2}
pre{background:#f4f4f5;padding:12px;white-space:pre-wrap;font-size:15px;overflow-wrap:anywhere;margin:0}
a{color:#1d4ed8}
.acoes{margin:16px 0}
@media (max-width:480px){h1{font-size:24px}th[scope=row]{width:100%;display:block;border-bottom:0;padding-bottom:0}td{display:block;padding-top:2px}}
"""


def e(x):
    return html.escape(str(x), quote=True)

def origem_valida(cab):
    """Anti-CSRF sem segredo. Navegadores atuais enviam Sec-Fetch-Site; sem ele, confere Origin com Host."""
    sfs = cab.get('Sec-Fetch-Site', '')
    if sfs:
        return sfs in ('same-origin', 'none')
    origem = cab.get('Origin', '')
    if origem:
        return urllib.parse.urlparse(origem).netloc == cab.get('Host', '')
    return True



def frame(titulo, corpo):
    return ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s · Economia</title><style>%s</style></head><body><main>%s</main></body></html>') % (e(titulo), CSS, corpo)


def linha(rotulo, valor):
    return '<tr><th scope="row">%s</th><td>%s</td></tr>' % (e(rotulo), e(valor))


def secao_perfis(d, ativo):
    cards = []
    for chave, p in PERFIS.items():
        marcado = ' aria-current="true"' if chave == ativo else ''
        cards.append('<form method="post" action="/acao/perfil"><input type="hidden" name="perfil" value="%s">'
                     '<button class="perfil" type="submit"%s><span class="titulo">%s</span><span>%s</span></button></form>'
                     % (chave, marcado, e(p['nome']), e(p['descricao'])))
    titulo = PERFIS[ativo]['nome'] if ativo else 'Personalizado'
    return ('<section aria-labelledby="h-perfis"><h2 id="h-perfis">Perfil ativo: <strong>%s</strong></h2>'
            '<p>Um clique aplica todos os valores do perfil e salva a escolha.</p><div class="perfis">%s</div></section>'
            % (e(titulo), ''.join(cards)))


def secao_estado(d):
    g = d['gpu']
    usos = ', '.join('%s (%s, PID %s)' % (u['processo'], u['container'], u['pid']) for u in d['gpu_em_uso_por']) or 'nenhum'
    cpu_w = '%.2f W' % d['cpu_potencia_w'] if d['cpu_potencia_w'] is not None else 'medindo...'
    rows = [
        linha('Potência medida da CPU', cpu_w),
        linha('Limite configurado', d['cpu_w_config']),
        linha('Limite da CPU (MSR)', d['limite_msr']),
        linha('Limite da CPU (MMIO)', d['limite_mmio']),
        linha('Turbo', d['turbo']),
        linha('Frequência cpu0', '%d MHz' % d['freq_cpu0_mhz']),
        linha('EPP / governor', d['epp_governor']),
        linha('ASPM (policy)', d['aspm']),
        linha('RTX', d['rtx_status']),
        linha('GPU em uso por', usos),
        linha('Diagnóstico GPU (lspci)', g['lspci']),
        linha('lsmod nvidia', g['lsmod_nvidia']),
        linha('Nós /dev/nvidia', g['dev_nvidia']),
        linha('Driver NVIDIA', g['driver_version']),
        linha('nvidia-smi', g['nvidia_smi']),
        linha('nvidia-container-runtime', g['nvidia_container_runtime']),
        linha('Docker daemon.json', g['docker_daemon_json']),
        linha('Bateria', d['bateria']),
        linha('Potência da bateria', '%s W' % d['potencia_bateria_w'] if d['potencia_bateria_w'] is not None else 'n/d'),
        linha('Tomada', d['tomada']),
        linha('Limite de carga', d['limite_bateria']),
        linha('Platform profile', d['platform_profile']),
        linha('Fans', d['fans']),
        linha('acer_wmi', d['acer_wmi']),
        linha('Temperaturas', d['temperaturas']),
        linha('Atualizado', d['atualizado']),
    ]
    return ('<section aria-labelledby="h-estado"><h2 id="h-estado">Estado atual</h2>'
            '<table><tbody>%s</tbody></table></section>' % ''.join(rows))


def faixa_cpu(nome, rotulo, v, maximo, fabrica):
    txt = '%d W (fábrica)' % v if fabrica else '%d W' % v
    return ('<label class="campo" for="%s">%s: <output id="o-%s">%s</output></label>'
            '<input id="%s" name="%s" type="range" min="%d" max="%d" step="1" value="%d" '
            'aria-describedby="d-%s" '
            'oninput="document.getElementById(\'o-%s\').textContent=this.value+\' W\'">'
            '<p class="ajuda" id="d-%s">Faixa: %d a %d W. O teto é o limite de fábrica do firmware.</p>') % (
        nome, e(rotulo), nome, e(txt), nome, nome, CPU_MIN_W, maximo, v, nome, nome, nome, CPU_MIN_W, maximo)


def bloco(acao, legenda, corpo, botao, classe=''):
    return ('<form method="post" action="%s" class="ctl"><fieldset><legend>%s</legend>%s'
            '<button type="submit" class="%s">%s</button></fieldset></form>') % (acao, e(legenda), corpo, classe, e(botao))


def radios(nome, opcoes, atual):
    return ''.join('<label class="op"><input type="radio" name="%s" value="%s"%s> %s</label>'
                   % (nome, e(v), ' checked' if v == atual else '', e(t)) for v, t in opcoes)


def select(ident, rotulo, opcoes, atual, campo='valor'):
    itens = ''.join('<option value="%s"%s>%s</option>' % (e(v), ' selected' if v == atual else '', e(t)) for v, t in opcoes)
    return '<label class="campo" for="%s">%s</label><select id="%s" name="%s">%s</select>' % (ident, e(rotulo), ident, campo, itens)


def secao_controles(d):
    lim_ = d['limites']
    o = d['opcoes']
    est = ESTADO
    pl1 = lim_['long'] if est['cpu_pl1'] == 'fabrica' else int(round(est['cpu_pl1']))
    pl2 = lim_['short'] if est['cpu_pl2'] == 'fabrica' else int(round(est['cpu_pl2']))
    cpu = bloco('/acao/cpu', 'Limites de potência da CPU',
                '<p class="atual">Atual: %s</p>' % e(descricao_cpu(est)) +
                faixa_cpu('pl1', 'Contínuo (PL1)', pl1, lim_['long'], est['cpu_pl1'] == 'fabrica') +
                faixa_cpu('pl2', 'Rajada (PL2)', pl2, lim_['short'], est['cpu_pl2'] == 'fabrica') +
                '<p class="ajuda">A rajada (PL2) não pode ficar abaixo do contínuo (PL1).</p>',
                'Aplicar limites')
    cpu += ('<form method="post" action="/acao/cpu" class="ctl"><input type="hidden" name="pl1" value="fabrica">'
            '<input type="hidden" name="pl2" value="fabrica"><button type="submit" class="secundario">Voltar ao limite de fábrica</button></form>')
    turbo = bloco('/acao/turbo', 'Turbo do processador',
                  '<p class="atual">Atual: %s</p>' % ('ligado' if est['turbo'] == 'on' else 'desligado') +
                  radios('valor', [('on', 'Ligado'), ('off', 'Desligado')], est['turbo']), 'Aplicar turbo')
    rtx = bloco('/acao/rtx', 'RTX (GPU NVIDIA)',
                '<p class="atual">Atual: %s</p>' % ('ligada' if est['rtx'] == 'on' else 'removida do barramento') +
                radios('valor', [('on', 'Ligada no barramento'), ('off', 'Removida do barramento')], est['rtx']) +
                '<p class="ajuda">Se a GPU estiver em uso, o painel pede confirmação antes de remover.</p>',
                'Aplicar RTX')
    fans_vals = [('auto', 'Automático (compara quiet e low-power)')]
    for alias, pref in (('quiet', 'Silencioso'), ('performance', 'Desempenho')):
        r = resolver_fans(alias, o['fans'])
        if r:
            fans_vals.append((alias, '%s (%s)' % (pref, ROT_FANS.get(r, r))))
    for c in o['fans']:
        if c not in [v for v, _ in fans_vals]:
            fans_vals.append((c, ROT_FANS.get(c, c)))
    atual_fans = est['fans'] if est['fans'] in [v for v, _ in fans_vals] else 'auto'
    if o['fans']:
        fans = bloco('/acao/fans', 'Perfil de fans',
                     '<p class="atual">Atual: %s</p>' % e(dict(fans_vals).get(atual_fans, atual_fans)) +
                     select('fans', 'Modo de fans', fans_vals, atual_fans), 'Aplicar fans')
    else:
        fans = bloco('/acao/fans', 'Perfil de fans', '<p>Indisponível neste sistema.</p>', 'Aplicar fans')
    epp = _select_bloco('/acao/epp', 'EPP (preferência de energia)', o['epp'], ROT_EPP, est['epp'], 'Aplicar EPP', 'epp', 'Preferência de energia')
    gov = _select_bloco('/acao/governor', 'Governor', o['governor'], ROT_GOV, est['governor'], 'Aplicar governor', 'governor', 'Governor de frequência')
    aspm = _select_bloco('/acao/aspm', 'ASPM (energia do PCIe)', o['aspm'], ROT_ASPM, est['aspm'], 'Aplicar ASPM', 'aspm', 'Política ASPM')
    return ('<section aria-labelledby="h-controles"><h2 id="h-controles">Controles</h2>'
            '<p class="ajuda">Cada controle aplica só o seu item e salva. Os limites da CPU ficam sempre dentro da faixa segura.</p>'
            '%s%s%s%s%s%s%s</section>') % (cpu, turbo, rtx, fans, epp, gov, aspm)


def _select_bloco(acao, legenda, opcoes, rot, atual, botao, ident, rotulo):
    if not opcoes:
        return bloco(acao, legenda, '<p>Indisponível neste sistema.</p>', botao)
    vals = [(v, rot.get(v, v)) for v in opcoes]
    atual_v = atual if atual in opcoes else opcoes[0]
    return bloco(acao, legenda, '<p class="atual">Atual: %s</p>' % e(rot.get(atual, atual)) +
                 select(ident, rotulo, vals, atual_v), botao)


def secao_registro():
    return '<section aria-labelledby="h-reg"><h2 id="h-reg">Registro</h2><pre>%s</pre></section>' % e('\n'.join(LOG[-12:]))


def pagina(d, aviso=None):
    corpo = ['<header><h1>Economia de energia</h1><p>Atualizado %s · <a href="/">Atualizar estado</a> · <a href="/status.json">Ver estado em JSON</a>%s</p></header>'
             % (e(d['atualizado']), ' · <strong>Modo simulado: nada é alterado</strong>' if SIM else '')]
    if aviso:
        corpo.append('<p class="aviso %s" role="status">%s</p>' % (aviso[0], e(aviso[1])))
    corpo.append(secao_perfis(d, perfil_ativo(ESTADO)))
    corpo.append(secao_estado(d))
    corpo.append(secao_controles(d))
    corpo.append(secao_registro())
    return frame('Economia', ''.join(corpo))


def pagina_confirmacao(caminho, form, usos):
    linhas = ''.join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td></tr>' % (e(u['processo']), e(u['container']), e(u['pid'])) for u in usos)
    ocultos = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k), e(v)) for k, v in form.items() if k != 'confirmar')
    corpo = ('<header><h1>Confirmar remoção da RTX</h1></header>'
             '<p class="aviso erro" role="alert">A GPU NVIDIA está em uso. Remover a RTX agora pode derrubar os processos abaixo, inclusive containers.</p>'
             '<table><thead><tr><th scope="col">Processo</th><th scope="col">Container</th><th scope="col">PID</th></tr></thead><tbody>%s</tbody></table>'
             '<form method="post" action="%s" class="acoes">%s<input type="hidden" name="confirmar" value="1">'
             '<button class="perigo" type="submit">Remover RTX mesmo assim</button></form>'
             '<p><a href="/">Cancelar e voltar</a></p>') % (linhas, e(caminho), ocultos)
    return frame('Confirmar remoção da RTX', corpo)


class Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class H(http.server.BaseHTTPRequestHandler):
    server_version = 'economia'

    def _enviar(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _texto(self, code, txt):
        self._enviar(code, txt.encode('utf-8'), 'text/plain; charset=utf-8')

    def _html(self, code, txt):
        self._enviar(code, txt.encode('utf-8'), 'text/html; charset=utf-8')

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)
        if p.path == '/status.json' or qs.get('format', [''])[0].lower() == 'json' or 'application/json' in self.headers.get('Accept', ''):
            body = json.dumps(status_data(), indent=2, ensure_ascii=False).encode('utf-8')
            return self._enviar(200, body, 'application/json; charset=utf-8')
        if p.path in ('/', '/index.html'):
            with LOCK:
                aviso = FLASH['v']
                FLASH['v'] = None
                d = status_data()
            return self._html(200, pagina(d, aviso))
        self._texto(404, 'Não encontrado.')

    def do_POST(self):
        caminho = urllib.parse.urlparse(self.path).path
        acao = ROTAS_POST.get(caminho)
        if acao is None:
            return self._texto(404, 'Não encontrado.')
        try:
            n = int(self.headers.get('Content-Length') or '0')
        except ValueError:
            n = -1
        if n < 0 or n > 4096:
            return self._texto(413, 'Requisição grande demais.')
        tipo = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if tipo != 'application/x-www-form-urlencoded':
            return self._texto(415, 'Tipo de conteúdo não aceito.')
        try:
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode('utf-8', 'replace'),
                                                             keep_blank_values=True, max_num_fields=32).items()}
        except ValueError:
            return self._texto(400, 'Formulário inválido.')
        if not origem_valida(self.headers):
            return self._texto(403, 'Requisição bloqueada: origem diferente do painel. Recarregue a página.')
        confirmar = form.get('confirmar') == '1'
        res = {}
        try:
            res = acao(form, confirmar)
        except ErroValidacao as x:
            FLASH['v'] = ('erro', str(x))
        except Exception as x:
            log('erro na ação %s: %s' % (caminho, x))
            FLASH['v'] = ('erro', 'Falha interna ao aplicar; veja o registro.')
        if res.get('confirmar'):
            return self._html(200, pagina_confirmacao(caminho, form, res['usos']))
        self._enviar(303, b'', 'text/plain; charset=utf-8', {'Location': '/'})

    def log_message(self, fmt, *args):
        return


def servir():
    while True:
        try:
            with Servidor((BIND, PORTA), H) as srv:
                log('painel em http://%s:%d' % (BIND, PORTA))
                srv.serve_forever()
        except Exception as x:
            log('servidor: %s' % x)
            time.sleep(10)


def main():
    if SIM:
        criar_simulacao()
        log('MODO SIMULADO: nada é tocado no hardware; raiz %s' % SIM_DIR)
    carregar_estado()
    threading.Thread(target=servir, daemon=True).start()
    threading.Thread(target=meter, daemon=True).start()
    probe()
    try:
        setup_fans()
    except Exception as x:
        log('setup_fans: %s' % x)
    while True:
        try:
            apply()
        except Exception as x:
            log('apply: %s' % x)
        with LOCK:
            log('limites: MSR %s | MMIO %s | turbo %s | ASPM %s | bateria %s | perfil %s'
                % (lim(ZONES[0]), lim(ZONES[1]), ESTADO['turbo'], rd(ASPM), BAT['v'], nome_perfil(ESTADO)))
        time.sleep(300)


if __name__ == '__main__':
    main()
