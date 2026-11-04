#!/usr/bin/env python3
"""
Testes unitários para llmproxy.py (agregador de SSE para JSON chat.completion).
"""

import unittest
import json
from llmproxy import (
    aggregate_sse_chunks,
    clean_json_markdown,
    extract_first_json_object,
    extract_numbered_list_items,
    build_object_from_numbered_list,
    repair_json_content,
    inspect_request_for_json,
    add_system_json_instruction,
    repair_completion_response,
    conform_object_to_schema,
)
class TestSSEAggregator(unittest.TestCase):
    def test_aggregate_content_and_ignore_reasoning(self):
        sse_input = """
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1793777209,"model":"gemini-3.8-flash-n","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1793777209,"model":"gemini-3.8-flash-n","choices":[{"index":0,"delta":{"reasoning_content":"Thinking about user preferences..."},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1793777215,"model":"gemini-3.8-flash-n","choices":[{"index":0,"delta":{"content":"Olá, "},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1793777215,"model":"gemini-3.8-flash-n","choices":[{"index":0,"delta":{"content":"mundo!"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1793777215,"model":"gemini-3.8-flash-n","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}

data: [DONE]
"""
        res = aggregate_sse_chunks(sse_input)
        self.assertEqual(res["id"], "chatcmpl-123")
        self.assertEqual(res["object"], "chat.completion")
        self.assertEqual(res["model"], "gemini-3.8-flash-n")
        self.assertEqual(len(res["choices"]), 1)
        choice = res["choices"][0]
        self.assertEqual(choice["index"], 0)
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertEqual(choice["message"]["content"], "Olá, mundo!")
        self.assertNotIn("reasoning_content", choice["message"])
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertIn("usage", res)
        self.assertEqual(res["usage"]["total_tokens"], 15)

    def test_aggregate_tool_calls(self):
        sse_input = """
data: {"id":"chatcmpl-tc","object":"chat.completion.chunk","created":100,"model":"gemini-model","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
data: {"id":"chatcmpl-tc","object":"chat.completion.chunk","created":100,"model":"gemini-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}
data: {"id":"chatcmpl-tc","object":"chat.completion.chunk","created":100,"model":"gemini-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": "}}]},"finish_reason":null}]}
data: {"id":"chatcmpl-tc","object":"chat.completion.chunk","created":100,"model":"gemini-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"Brasília\\"}"}}]},"finish_reason":null}]}
data: {"id":"chatcmpl-tc","object":"chat.completion.chunk","created":100,"model":"gemini-model","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]
"""
        res = aggregate_sse_chunks(sse_input)
        choice = res["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertIn("tool_calls", choice["message"])
        self.assertEqual(len(choice["message"]["tool_calls"]), 1)
        tc = choice["message"]["tool_calls"][0]
        self.assertEqual(tc["id"], "call_abc")
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "get_weather")
        self.assertEqual(tc["function"]["arguments"], '{"city": "Brasília"}')

    def test_empty_or_whitespace_sse(self):
        res = aggregate_sse_chunks("")
        self.assertEqual(res["object"], "chat.completion")
        self.assertEqual(len(res["choices"]), 1)
        self.assertEqual(res["choices"][0]["message"]["content"], "")


class TestJSONExtractionAndRepair(unittest.TestCase):
    def setUp(self):
        self.rewrite_schema = {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alternative search queries"
                },
                "dateRange": {
                    "type": "object",
                    "properties": {
                        "start": {"type": ["string", "null"]},
                        "end": {"type": ["string", "null"]}
                    },
                    "required": ["start", "end"]
                },
                "hasTemporalQuery": {
                    "type": "boolean",
                    "description": "Whether query contains temporal info"
                }
            },
            "required": ["queries", "dateRange", "hasTemporalQuery"]
        }

    def test_clean_json_markdown(self):
        fenced = "```json\n{\"foo\": \"bar\"}\n```"
        self.assertEqual(clean_json_markdown(fenced), '{"foo": "bar"}')
        fenced_no_lang = "```\n[1, 2, 3]\n```"
        self.assertEqual(clean_json_markdown(fenced_no_lang), '[1, 2, 3]')

    def test_extract_first_json_object(self):
        text_with_preamble = "Here is the response you asked for:\n{\"key\": \"val\", \"escaped\": \"a\\\"b\"}\nHope this helps!"
        obj = extract_first_json_object(text_with_preamble)
        self.assertEqual(obj, {"key": "val", "escaped": 'a"b'})

    def test_extract_numbered_list_items(self):
        text = """1. "primeira query"
2. segunda query
3) "terceira query com aspas" """
        items = extract_numbered_list_items(text)
        self.assertEqual(items, ["primeira query", "segunda query", "terceira query com aspas"])

    def test_build_object_from_gemini_markdown_error(self):
        # Caso real observado no server.log que disparava AI_NoObjectGeneratedError
        raw_gemini = """**Alternative Search Queries:**
1. "editor de código e tema favorito nan"
2. "qual editor e tema nan usa setup"
3. "nan dotfiles editor de texto tema preferido"

**Temporal Information:**
* None detected."""
        res = build_object_from_numbered_list(raw_gemini, self.rewrite_schema)
        self.assertIn("queries", res)
        self.assertEqual(len(res["queries"]), 3)
        self.assertEqual(res["queries"][0], "editor de código e tema favorito nan")
        self.assertEqual(res["queries"][1], "qual editor e tema nan usa setup")
        self.assertEqual(res["queries"][2], "nan dotfiles editor de texto tema preferido")
        self.assertEqual(res["hasTemporalQuery"], False)
        self.assertEqual(res["dateRange"], {"start": None, "end": None})

    def test_repair_json_content_with_code_fences(self):
        content = "```json\n{\"queries\": [\"a\", \"b\"], \"hasTemporalQuery\": false, \"dateRange\": {\"start\": null, \"end\": null}}\n```"
        repaired = repair_json_content(content, self.rewrite_schema)
        parsed = json.loads(repaired)
        self.assertEqual(parsed["queries"], ["a", "b"])

    def test_repair_json_content_with_markdown_list(self):
        raw_gemini = """**Alternative Search Queries:**
1. "query A"
2. "query B"

**Temporal Information:**
* None detected."""
        repaired = repair_json_content(raw_gemini, self.rewrite_schema)
        parsed = json.loads(repaired)
        self.assertEqual(parsed["queries"], ["query A", "query B"])
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertIsNone(parsed["dateRange"]["start"])

    def test_inspect_and_add_system_json_instruction(self):
        req_body = {
            "model": "gemini-3.8-flash-high",
            "messages": [{"role": "user", "content": "rewrite this query"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"schema": self.rewrite_schema}
            }
        }
        wants_json, schema = inspect_request_for_json(req_body)
        self.assertTrue(wants_json)
        self.assertEqual(schema, self.rewrite_schema)

        modified = add_system_json_instruction(req_body, schema)
        self.assertEqual(len(modified["messages"]), 2)
        self.assertEqual(modified["messages"][0]["role"], "system")
        self.assertIn("CRITICAL REQUIREMENT", modified["messages"][0]["content"])
        self.assertIn("JSON Schema to follow strictly", modified["messages"][0]["content"])

    def test_inspect_request_stream_ignored(self):
        req_body = {
            "stream": True,
            "response_format": {"type": "json_object"}
        }
        wants_json, _ = inspect_request_for_json(req_body)
        self.assertFalse(wants_json)

    def test_repair_completion_response(self):
        raw_completion = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "```json\n{\"ok\": true}\n```"
                    },
                    "finish_reason": "stop"
                }
            ]
        }
        repaired = repair_completion_response(raw_completion)
        self.assertEqual(repaired["choices"][0]["message"]["content"], '{"ok": true}')

    def test_conform_object_to_schema_aliases_and_defaults(self):
        gemini_json = '{"rewritten_queries": ["query 1", "query 2"]}'
        repaired = repair_json_content(gemini_json, self.rewrite_schema)
        parsed = json.loads(repaired)
        self.assertEqual(parsed["queries"], ["query 1", "query 2"])
        self.assertNotIn("rewritten_queries", parsed)
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertEqual(parsed["dateRange"], {"start": None, "end": None})

    def test_repair_real_server_log_case_1_without_schema(self):
        raw = '{"original_query": "automação residencial e preferências do nan", "alternative_queries": ["automação residencial preferências do usuário", "personalização e preferências em automação residencial", "configuração de perfis e preferências smart home"], "date_range": null}'
        repaired = repair_json_content(raw, schema=None)
        parsed = json.loads(repaired)
        self.assertIn("queries", parsed)
        self.assertIn("automação residencial e preferências do nan", parsed["queries"])
        self.assertEqual(len(parsed["queries"]), 4)
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertEqual(parsed["dateRange"], {"start": None, "end": None})

    def test_repair_real_server_log_case_2_without_schema(self):
        raw = '{"original_query": "projetos do github e homelab", "rewritten_queries": ["awesome selfhosted homelab github projects", "homelab github repositories self-hosted setup", "melhores projetos open source homelab github"], "temporal_info": null}'
        repaired = repair_json_content(raw, schema=None)
        parsed = json.loads(repaired)
        self.assertIn("queries", parsed)
        self.assertIn("projetos do github e homelab", parsed["queries"])
        self.assertEqual(len(parsed["queries"]), 4)
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertEqual(parsed["dateRange"], {"start": None, "end": None})

    def test_repair_numbered_list_without_schema(self):
        raw = "**Alternative Queries:**\n1. query A\n2. query B"
        repaired = repair_json_content(raw, schema=None, is_query_rewrite=True)
        parsed = json.loads(repaired)
        self.assertIn("queries", parsed)
        self.assertEqual(parsed["queries"], ["query A", "query B"])
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertEqual(parsed["dateRange"], {"start": None, "end": None})

    def test_repair_negative_non_rewrite_json_untouched(self):
        raw = '{"updated_memories": ["memoria 1", "memoria 2"], "candidates": 5}'
        repaired = repair_json_content(raw, schema=None, is_query_rewrite=False)
        parsed = json.loads(repaired)
        self.assertEqual(parsed, {"updated_memories": ["memoria 1", "memoria 2"], "candidates": 5})
        self.assertNotIn("queries", parsed)
        self.assertNotIn("hasTemporalQuery", parsed)
    def test_clean_json_markdown_unclosed_and_preamble(self):
        unclosed = "```json\n{\"status\": \"ok\", \"unclosed\": true}"
        cleaned = clean_json_markdown(unclosed)
        self.assertEqual(json.loads(cleaned), {"status": "ok", "unclosed": True})

        preamble = "Here is the JSON output:\n```json\n{\"result\": [1, 2, 3]}\n```\nEnd of output."
        cleaned_pre = clean_json_markdown(preamble)
        self.assertEqual(json.loads(cleaned_pre), {"result": [1, 2, 3]})

    def test_inspect_request_prompt_asks_json(self):
        # Prompt pede JSON em mensagem de usuario
        body_user_json = {"messages": [{"role": "user", "content": "Por favor gere a resposta em JSON puro."}]}
        wants_json, _ = inspect_request_for_json(body_user_json)
        self.assertTrue(wants_json)

        # Prompt de reescrita de busca
        body_rewrite = {"messages": [{"role": "user", "content": "Rewrite search queries for knowledge base"}]}
        wants_json, _ = inspect_request_for_json(body_rewrite)
        self.assertTrue(wants_json)

        # response_format como string
        body_fmt_str = {"messages": [{"role": "user", "content": "dados"}], "response_format": "json"}
        wants_json, _ = inspect_request_for_json(body_fmt_str)
        self.assertTrue(wants_json)

        # Prompt comum sem pedido de JSON
        body_plain = {"messages": [{"role": "user", "content": "Conte uma curiosidade sobre o espaço sideral."}]}
        wants_json, _ = inspect_request_for_json(body_plain)
        self.assertFalse(wants_json)

    def test_repair_forces_pure_json_without_markdown_fences(self):
        raw_with_fences = "```json\n{\"title\": \"Teste\", \"items\": [\"a\", \"b\"]}\n```"
        completion = {
            "id": "chatcmpl-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": raw_with_fences}}]
        }
        repaired = repair_completion_response(completion)
        content = repaired["choices"][0]["message"]["content"]
        self.assertNotIn("```", content)
        self.assertTrue(content.startswith("{"))
        self.assertTrue(content.endswith("}"))
        parsed = json.loads(content)
        self.assertEqual(parsed, {"title": "Teste", "items": ["a", "b"]})

    def test_repair_fenced_gemini_query_rewrite_fixes_failure(self):
        # Caso que falhava o query rewrite do supermemory-server
        gemini_fenced = "```json\n{\n  \"rewritten_queries\": [\n    \"supermemory umbrel local\",\n    \"configuracao 9router\"\n  ]\n}\n```"
        completion = {
            "id": "chatcmpl-qr",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": gemini_fenced}}]
        }
        repaired = repair_completion_response(completion, is_query_rewrite=True)
        content = repaired["choices"][0]["message"]["content"]
        self.assertNotIn("```", content)
        parsed = json.loads(content)
        self.assertIn("queries", parsed)
        self.assertIn("supermemory umbrel local", parsed["queries"])
        self.assertEqual(parsed["hasTemporalQuery"], False)
        self.assertEqual(parsed["dateRange"], {"start": None, "end": None})
    def test_repair_json_content_preserves_plain_text_without_fences(self):
        plain = "Esta é uma resposta em texto puro sem formato JSON."
        repaired = repair_json_content(plain, schema=None, is_query_rewrite=False)
        self.assertEqual(repaired, plain)

        fenced_plain = "```\nTexto com crases de markdown sem json.\n```"
        repaired_fenced = repair_json_content(fenced_plain, schema=None, is_query_rewrite=False)
        self.assertEqual(repaired_fenced, "Texto com crases de markdown sem json.")

    def test_inspect_request_distinguishes_casual_mention_from_json_request(self):
        # Casual mention em user message NÃO ativa wants_json
        body_casual = {"messages": [{"role": "user", "content": "O que você acha de json em comparação com yaml?"}]}
        wants_json, _ = inspect_request_for_json(body_casual)
        self.assertFalse(wants_json)

        # Rewrite casual que não é busca
        body_rewrite_email = {"messages": [{"role": "user", "content": "Rewrite this email to make it friendly."}]}
        wants_json, _ = inspect_request_for_json(body_rewrite_email)
        self.assertFalse(wants_json)

        # System prompt com JSON ativa wants_json
        body_sys_json = {"messages": [{"role": "system", "content": "You are a memory agent. Output in valid JSON."}]}
        wants_json, _ = inspect_request_for_json(body_sys_json)
        self.assertTrue(wants_json)

        # User com comando explícito ativa wants_json
        body_cmd_json = {"messages": [{"role": "user", "content": "Please output as JSON object."}]}
        wants_json, _ = inspect_request_for_json(body_cmd_json)
        self.assertTrue(wants_json)
if __name__ == "__main__":
    unittest.main()
