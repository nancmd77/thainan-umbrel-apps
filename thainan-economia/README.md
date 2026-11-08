# Economia de energia (thainan-economia) 1.2.0

Painel de controle de energia para o notebook Acer Nitro (RTX 2050), rodando no Umbrel.
Porta interna 8536, exposta pelo app proxy do Umbrel, que exige login.

## Perfis (um clique)

| Perfil | CPU | Turbo | RTX | Fans | EPP | Governor | ASPM |
|---|---|---|---|---|---|---|---|
| Economia | 7 W / 7 W | desligado | removida | quiet | power | powersave | powersupersave |
| Equilíbrio | PL1 35 W / PL2 55 W | ligado | ligada | auto | balance_power | powersave | powersave |
| Desempenho | limites de fábrica | ligado | ligada | performance | performance | powersave | performance |
| Fábrica | limites de fábrica | ligado | ligada | auto | power | powersave | powersupersave |

- Perfil ativo aparece no topo; se nenhum perfil bater com os valores atuais, aparece "Personalizado".
- Fans: se o notebook não aceitar o perfil pedido, cai para `auto` e mostra aviso.
- Governor fica em `powersave` em Desempenho para não travar a frequência.
- EPP com governor `performance`: o intel_pstate recusa a escrita (EBUSY) e o EPP fica preso. Por isso, ao mudar o EPP com governor `performance`, o governor vai para `powersave` (nota na página e linha no registro). Governor `performance` escolhido sozinho mostra aviso de que o EPP fica preso.

## Controles individuais

Cada controle é um formulário com POST próprio: limites de CPU (PL1/PL2), turbo, RTX, fans, EPP, governor e ASPM.

- CPU: faixa de 5 W até o teto de fábrica (`constraint_N_max_power_uw`). PL2 não pode ficar abaixo de PL1. Token `fabrica` volta ao limite de fábrica.
- RTX: remover a GPU do barramento pede confirmação se ela estiver em uso (o loop de 300 s adia e registra).

## Tema

- Seletor "Tema" no topo: Claro, Escuro ou Sistema (padrão). Sistema segue `prefers-color-scheme` do navegador e atualiza ao vivo.
- Escolha salva só no navegador, em `localStorage` (`economia-tema`). Não passa pelo servidor nem muda o estado do Nonve.
- Script de tema roda no `<head>` antes do CSS, então a página não pisca no tema errado.
- Cores em `TEMAS` (`src/economia.py`), um par claro/escuro de tokens `--cor-*`. Testes conferem contraste WCAG AA (texto 4,5:1; bordas de controle 3:1).

## Estado

- Salvo em `/data/estado.json` (volume `${APP_DATA_DIR}/data:/data`), escrita atômica.
- O estado salvo tem prioridade. As variáveis `CPU_W`, `TURBO` e `RTX` só semeiam o padrão na primeira execução.
- A cada 300 s e no boot, os valores salvos são reaplicados.

## GPU RTX 2050

- Mostra potência, limite, temperatura, P-state e utilização sem acordar a GPU.
- Lê sysfs primeiro (`/sys/bus/pci/devices/0000:01:00.0/power/runtime_status`, `power_state`, `power/runtime_suspended_time`, `power/runtime_active_time`). Não abre `/dev/nvidia*`.
- `nvidia-smi` só roda com `runtime_status == active`. Dormindo (D3cold) ou ausente: não chama o binário e mostra "cerca de 0 W".
- Medição no máximo a cada 30 s, só ao abrir a página ou o `/status.json`. Não impede o runtime suspend.
- "Tempo dormindo desde o boot" = `suspended / (suspended + active)`, a partir dos contadores do runtime PM.
- Compose: monta `/usr/bin` em `/host/bin` e `/usr/lib/x86_64-linux-gnu` em `/host/lib` (somente leitura). O `nvidia-smi` do host roda pelo loader do host (`ld-linux-x86-64.so.2`), porque a imagem é Alpine.
- Campo `gpu` do JSON é o bloco novo de energia. O diagnóstico antigo (lspci, driver, módulos) foi para `gpu_diagnostico`.

## Segurança

- Controles só por POST. Body até 4096 bytes. Só `application/x-www-form-urlencoded`.
- Validação de todos os campos no servidor. Entrada do usuário nunca vai para shell.
- Proteção contra CSRF por origem (sem token): se `Sec-Fetch-Site` existir, exige `same-origin` ou `none`; senão, se `Origin` existir, precisa bater com `Host`. Requisição de outra origem recebe 403.
- Cabeçalhos: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP restritiva com `form-action 'self'`.

## Atualização da página

Não há refresh automático (um `<meta refresh>` quebraria formulários em edição). Use o link "Atualizar".

## Modo simulado (desenvolvimento)

```sh
ECONOMIA_SIMULADO=1 BIND=127.0.0.1 ECONOMIA_PORTA=18536 python3 thainan-economia/src/economia.py
```

- Não toca em sysfs, PCI nem acer_wmi. Comandos de sistema retornam 127.
- `ECONOMIA_SIM_DIR` (default `/tmp/economia-simulado`, recriado a cada start).
- `ECONOMIA_SIM_GPU_EM_USO` (default `1`) simula GPU em uso.

## Testes

```sh
python3 -m unittest discover -s thainan-economia/tests
```

Cobrem validadores, resolução de fans, combinação de perfis, origem de POST, estado em disco, painel simulado, renderização das páginas e sincronia do compose.

## Compose gerado

`docker-compose.yml` é gerado por `tools/gerar_compose.py`, que embute `src/economia.py` no compose (o Umbrel não entrega arquivos novos em updates).

```sh
python3 thainan-economia/tools/gerar_compose.py          # regera
python3 thainan-economia/tools/gerar_compose.py --check  # valida sincronia
```

Editar `src/economia.py` sem regerar quebra o teste de sincronia.
