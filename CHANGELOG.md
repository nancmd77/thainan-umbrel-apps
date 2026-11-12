# Histórico de alterações

Organizado por app. Cada entrada informa versão, data da tag anotada, o que mudou e como desfazer: voltar à tag anterior. As descrições abaixo vêm exclusivamente das mensagens das tags anotadas existentes, sem completar detalhes a partir de commits ou suposições. Reverter a versão não restaura dados; veja [como reverter](docs/REVERTER.md).

## thainan-video-kb

### 1.16.0 — 2026-11-09

- Tag: `video-kb-v1.16.0`.
- O que mudou: texto opcional via Mercury 3 direto na Inception (`texto_url`, `texto_key`, `model_texto`), sem consumir o 9Router. Visão e Decider continuam no 9Router. Erro, timeout ou JSON inválido na Inception tenta o modelo atual do 9Router uma vez. Temperatura de 0.5 a 1.0, sem seed; teste de credencial em Configurações.
- Complemento: `esforco_texto` (`instant` por padrão; `low`, `medium`, `high` opcionais) envia `reasoning_effort` só à Inception. Título, gancho, resumo, pontos, tags, tema, texto literal da tela, links e tópicos da síntese são validados antes dos cortes; excesso pede uma reescrita, persistência ou falha usa o fallback atual sem loop.
- Verificação local: 119 testes (117 passaram, 2 pulados, nenhuma falha); smoke de um resumo com `instant`, HTTP 200 em duas requisições (uma reescrita), 1,332 s, resumo com 280 caracteres. Sem texto ou chave no relatório; produção não alterada.
- YouTube: transcrição com Whisper Large v3 Turbo na Groq (`groq_key`, `groq_model`). Áudio mp3 mono 64k; acima de 24 MB, 32k a 16 kHz; ainda maior ou falha da Groq cai no Scribe se houver chave ElevenLabs. Linhas com tempo pelos segmentos do Whisper. Teste de credencial da Groq. 125 testes (2 pulados); smoke real com áudio de 7 s em 0,44 s.
- Como desfazer: apagar a chave da Inception no formulário (`-`) para voltar ao fluxo anterior, ou voltar a `video-kb-v1.15.0`.

## thainan-videokb (Selah)

### 0.10.0 — 2026-11-12

- Tag: `selah-v0.10.0`.
- O que mudou: comentários no card (resumo do público, perguntas por sentido respondidas com vídeo e comentários, pódio dos 3 mais relevantes, posturas Confirma/Acrescenta/Contesta, "Ler tudo" com desfoque); dados coletados de 11 reels em `dados/coment.json`, anexados pelo id do reel. Ficha com pontos-chave em linha do tempo, céu atrás dos comentários, emojis estilo iOS. Computador (≥1200 px): três cards de vidro (vídeo, ficha, comentários) sobre o céu, com desfoque nas bordas do scroll. Player com onda removido (vídeo usa os controles nativos). Retalho e Pavio saem do menu; tema Duna fixo. Leitor devolve o ícone do app (AlternativeTo).
- Como desfazer: voltar a `selah-v0.9.0`.

### 0.9.0 — 2026-11-11

- Tag: `selah-v0.9.0`.
- O que mudou: visual "Copilot" vira o padrão (`?copilot=0` desliga): trilho lateral fino com ícones, busca grande com Rápida e Por sentido (IA escolhe os salvos pelo assunto, `/search?modo=sentido`), celular no mesmo padrão. Tema Duna. Tipografia Ginto. Papiro: Product Hunt (Hoje, Ontem, Semana, Mês, Lançamentos) ao lado do AlternativeTo; as outras 12 fontes deixam de ser coletadas (histórico preservado). Leitor ganha `/producthunt` e baixa também `producthunt.py`; cache em `PH_CACHE_DIR`.
- Como desfazer: voltar a `selah-v0.8.3`.

### 0.8.3 — 2026-11-09

- Tag: `selah-v0.8.3`.
- O que mudou: no Papiro, tradução e resumo em pt vão ao Mercury 3 (modo instant) quando a config do Video KB tem a chave da Inception (`texto_key`); falha cai nos modelos do 9Router. Filtro por interesse (Decider) segue no 9Router.
- Pacote: 0.8.2 com `novas.js` novo (14 partes).
- Como desfazer: voltar a `selah-v0.8.2`.

## thainan-openbot

### 0.64.0-r7 — 2026-11-08

- Tag: `thainan-openbot-v0.64.0-r7`.
- O que mudou: md ask mais rápido (Qwen via Cerebras 9Router e reserva Gemini).
- Como desfazer: voltar a `thainan-openbot-v0.64.0-r6`.

### 0.64.0-r6 — 2026-11-08

- Tag: `thainan-openbot-v0.64.0-r6`.
- O que mudou: md mais confiável (tela vazia código 4, ask via Gemini 9Router, skills e troca_fabrica).
- Como desfazer: voltar a `thainan-openbot-v0.64.0-r5`.

### 0.64.0-r5 — 2026-11-08

- Tag: `thainan-openbot-v0.64.0-r5`.
- O que mudou: regra única para o ESCALAR do decide em todos os textos dos agentes. Decisão difícil de desfazer vai ao conselho; decisão do dia a dia, ao modelo grande de raciocínio. O Nan só é perguntado se o conselho também ficar com confiança baixa ou se a ação for destrutiva. Cópias de fábrica antigas trocadas por sha256 e edição do Nan preservada.
- Como desfazer: voltar a `thainan-openbot-v0.64.0-r4`.

### 0.64.0-r4 — 2026-11-08

- Tag: `thainan-openbot-v0.64.0-r4`.
- O que mudou: skills conselho, decide e moondream também em `~/.agents/skills` (o Chefe passa a achar o conselho) e os 4 casos do conselho no bloco gerenciado do `AGENTS.md`.
- Como desfazer: voltar a `thainan-openbot-v0.64.0-r3`.

### 0.64.0-r3 — 2026-11-08

- Tag: `thainan-openbot-v0.64.0-r3`.
- O que mudou: novo trio padrão do conselho (Gemini 3.8 Flash High presidente, gpt-oss-120b, Qwen 3.8 27B).
- Como desfazer: voltar a `thainan-openbot-v0.64.0-r2`.
