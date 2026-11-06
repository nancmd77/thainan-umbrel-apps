# Economia de energia (thainan-economia) 1.1.1

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
