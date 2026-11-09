# Histórico de alterações

Organizado por app. Cada entrada informa versão, data da tag anotada, o que mudou e como desfazer: voltar à tag anterior. As descrições abaixo vêm exclusivamente das mensagens das tags anotadas existentes, sem completar detalhes a partir de commits ou suposições. Reverter a versão não restaura dados; veja [como reverter](docs/REVERTER.md).

## thainan-video-kb

### 1.16.0 — próxima versão

- Tag prevista: `video-kb-v1.16.0` (Hark cria após o merge; ainda não publicada).
- O que mudou: texto opcional via Mercury 3 direto na Inception (`texto_url`, `texto_key`, `model_texto`), sem consumir o 9Router. Visão e Decider continuam no 9Router. Erro, timeout ou JSON inválido na Inception tenta o modelo atual do 9Router uma vez. Temperatura de 0.5 a 1.0, sem seed; teste de credencial em Configurações.
- Complemento: `esforco_texto` (`instant` por padrão; `low`, `medium`, `high` opcionais) envia `reasoning_effort` só à Inception. Título, gancho, resumo, pontos, tags, tema, texto literal da tela, links e tópicos da síntese são validados antes dos cortes; excesso pede uma reescrita, persistência ou falha usa o fallback atual sem loop.
- Verificação local: 119 testes (117 passaram, 2 pulados, nenhuma falha); smoke de um resumo com `instant`, HTTP 200 em duas requisições (uma reescrita), 1,332 s, resumo com 280 caracteres. Sem texto ou chave no relatório; produção não alterada.
- Como desfazer: apagar a chave da Inception no formulário (`-`) para voltar ao fluxo anterior, ou voltar a `video-kb-v1.15.0`.

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
