---
name: supermemory-nan
description: Use em toda conversa e toda tarefa do Nan. Memória compartilhada dele (Supermemory_PTBR no Nonve) pelo MCP supermemory do LiteLLM - buscar contexto antes de responder e gravar decisões, preferências e resultados no fim.
---

# Memória do Nan (Supermemory_PTBR)

Servidor MCP `supermemory` no LiteLLM, ligado ao app thainan-supermemory-pt (porta 8616). Namespace padrão: `sm_project_default`.

## Antes de responder
- Chame a ferramenta de busca (`postNsByNamespaceSearch`) com namespace `sm_project_default` e a pergunta em linguagem natural (2 ou 3 buscas curtas se o assunto for amplo).
- Opcional: `postNsByNamespaceProfile` para o perfil fixo do Nan.
- Use o que voltar sem citar a fonte. Se não voltar nada, siga normalmente.

## No fim da tarefa
- Grave com `postNsByNamespaceDocument` (namespace `sm_project_default`) uma frase curta em português: decisão, preferência ou resultado, com a data (AAAA-MM-DD). Metadata: `{"source": "litellm", "tipo": "fato" | "decisao" | "desfecho"}`.

## Nunca
- Gravar cartões, senhas, chaves, documentos de identidade.
- Apagar memórias ou namespaces (as ferramentas de apagar ficam bloqueadas).
- Imprimir ou pedir a chave do Supermemory.
