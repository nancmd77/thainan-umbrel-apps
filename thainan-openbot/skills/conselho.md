# Skill: conselho

Ferramenta CLI `conselho`: consulta 3 modelos (via 9Router) em paralelo sobre a mesma pergunta e pede a um presidente a síntese com recomendação, grau de confiança, pontos de concordância e divergência.

## Quando usar (só nestes 4 casos)
1. Decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados).
2. Dois agentes nichados discordam, ou plano e revisão discordam.
3. Confiança do `decide` abaixo de 0,35 (ele imprimiu `ESCALAR`).
4. O Nan pediu explicitamente "pergunta ao conselho".

Fora desses casos, não use. Nunca no dia a dia. Máximo 1 vez por tarefa. Só o Chefe chama o conselho; os subagentes não chamam.

## Comandos
```bash
# Pergunta simples
conselho "Vale migrar o banco agora ou depois da entrega?"

# Com contexto de arquivo
conselho "Aprovar esta mudança de schema?" --contexto /tmp/plano.md

# Contexto pela entrada padrão
cat /tmp/plano.md | conselho "Aprovar esta mudança de schema?" --contexto -

# Escolher modelos (separados por vírgula)
conselho "Escolher a rota de deploy?" --modelos ag/gemini-3.8-flash-high,cerebras/gpt-oss-120b,cerebras/qwen-3.8-27b

# Saída estruturada
conselho "Apagar a tabela antiga?" --json
```

Variáveis: `CONSELHO_MODELOS` (lista padrão), `CONSELHO_PRESIDENTE` (padrão: primeiro modelo da lista), `CONSELHO_TIMEOUT` (padrão 90 s por chamada).

## Como ler a saída
- `Recomendação`: a síntese final do presidente.
- `Confiança`: `alta`, `média` ou `baixa`. Com `baixa`, não aja sem confirmar com o Nan.
- `Concordam` / `Divergem`: o que os modelos afirmaram em comum e onde discordam.
- `Falhas`: modelos que não responderam (429, timeout, HTTP). Com 2 respostas, o conselho segue; com 1, a resposta vem com aviso e sem síntese.

## Regras
- Uma rodada só. Não repita a consulta com outras palavras para "ver se muda".
- Não envie segredos, chaves ou documentos pessoais na pergunta ou no contexto. O registro em `/data/conselho.log` guarda só data, pergunta resumida (até 200 caracteres), modelos, falhas e confiança.
- Código de saída `1`: nenhum modelo respondeu ou chave ausente. Registre a falha e siga sem o conselho.
- Código de saída `2`: nenhum presidente conseguiu sintetizar. As respostas brutas saem na saída padrão.
