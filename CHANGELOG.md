# Histórico de alterações

Organizado por app. Cada entrada informa versão, data da tag anotada, o que mudou e como desfazer: voltar à tag anterior. As descrições abaixo vêm exclusivamente das mensagens das tags anotadas existentes, sem completar detalhes a partir de commits ou suposições. Reverter a versão não restaura dados; veja [como reverter](docs/REVERTER.md).

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
