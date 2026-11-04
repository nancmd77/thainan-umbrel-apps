#!/usr/bin/env python3
"""
llmproxy.py - Proxy local OpenAI-compatível entre supermemory-server e 9Router.
Porta padrão: 127.0.0.1:20129 -> 9Router (padrão: http://192.168.18.171:20128).
Se o cliente NÃO pediu stream e a resposta vier SSE (text/event-stream ou data:),
agrega os chunks num objeto chat.completion JSON padrão (OpenAI), ignorando reasoning.
Se o cliente pediu stream, repassa tal qual.
"""

import sys
import os
import json
import re
import time
from typing import Optional, Tuple, Any, Dict, List
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
from urllib.parse import urlparse

_raw_target = os.environ.get("TARGET_BASE_URL", "http://192.168.18.171:20128").rstrip("/")
if _raw_target.endswith("/v1"):
    _raw_target = _raw_target[:-3]
TARGET_BASE_URL = _raw_target
LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "20129"))


def aggregate_sse_chunks(sse_text: str) -> dict:
    """
    Agrega chunks SSE (data: {...}) num objeto chat.completion padrão da OpenAI.
    Ignora reasoning/reasoning_content e preserva content, finish_reason, usage, tool_calls.
    """
    stripped = sse_text.strip()
    clean_text = stripped.replace("data: [DONE]", "").strip() if "data: [DONE]" in stripped else stripped
    if clean_text.startswith("{"):
        try:
            parsed = json.loads(clean_text)
            if isinstance(parsed, dict) and "choices" in parsed:
                for choice in parsed.get("choices", []):
                    msg = choice.get("message")
                    if isinstance(msg, dict):
                        msg.pop("reasoning", None)
                        msg.pop("reasoning_content", None)
                return parsed
        except Exception:
            pass

    first_id = None
    first_created = None
    first_model = None
    system_fingerprint = None
    choices_map = {}
    choice_tool_calls = {}
    last_usage = None

    for raw_line in sse_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            payload_str = line[5:].strip()
        elif line.startswith("{"):
            payload_str = line
        else:
            continue
        if not payload_str or payload_str == "[DONE]":
            continue
        try:
            chunk = json.loads(payload_str)
        except Exception:
            continue

        if first_id is None and chunk.get("id"):
            first_id = chunk["id"]
        if first_created is None and chunk.get("created"):
            first_created = chunk["created"]
        if first_model is None and chunk.get("model"):
            first_model = chunk["model"]
        if system_fingerprint is None and chunk.get("system_fingerprint"):
            system_fingerprint = chunk["system_fingerprint"]
        if chunk.get("usage"):
            last_usage = chunk["usage"]

        for choice_data in chunk.get("choices", []):
            idx = choice_data.get("index", 0)
            if idx not in choices_map:
                choices_map[idx] = {
                    "index": idx,
                    "message": {
                        "role": "assistant",
                        "content": "",
                    },
                    "finish_reason": None,
                }
                choice_tool_calls[idx] = {}

            delta = choice_data.get("delta", {})
            if delta.get("role"):
                choices_map[idx]["message"]["role"] = delta["role"]

            # Content agregado
            if delta.get("content"):
                choices_map[idx]["message"]["content"] += delta["content"]

            # Reasoning ignorado explicitamente (delta.get("reasoning_content") / delta.get("reasoning"))

            # Tool calls agregados
            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    tc_idx = tc.get("index", 0)
                    if tc_idx not in choice_tool_calls[idx]:
                        choice_tool_calls[idx][tc_idx] = {
                            "id": tc.get("id", ""),
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc.get("function", {}).get("name", "") if tc.get("function") else "",
                                "arguments": tc.get("function", {}).get("arguments", "") if tc.get("function") else "",
                            },
                        }
                    else:
                        if tc.get("id"):
                            choice_tool_calls[idx][tc_idx]["id"] = tc["id"]
                        if tc.get("type"):
                            choice_tool_calls[idx][tc_idx]["type"] = tc["type"]
                        fn = tc.get("function")
                        if fn:
                            if fn.get("name"):
                                choice_tool_calls[idx][tc_idx]["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                choice_tool_calls[idx][tc_idx]["function"]["arguments"] += fn["arguments"]

            if choice_data.get("finish_reason"):
                choices_map[idx]["finish_reason"] = choice_data["finish_reason"]

    choices_list = []
    if not choices_map:
        choices_list.append({
            "index": 0,
            "message": {"role": "assistant", "content": ""},
            "finish_reason": "stop",
        })
    else:
        for idx in sorted(choices_map.keys()):
            item = choices_map[idx]
            if idx in choice_tool_calls and choice_tool_calls[idx]:
                tcs = [choice_tool_calls[idx][k] for k in sorted(choice_tool_calls[idx].keys())]
                item["message"]["tool_calls"] = tcs
                if not item["message"]["content"]:
                    item["message"]["content"] = None
            if item["finish_reason"] is None:
                item["finish_reason"] = "stop"
            choices_list.append(item)

    result = {
        "id": first_id or f"chatcmpl-proxy-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": first_created or int(time.time()),
        "model": first_model or "unknown",
        "choices": choices_list,
    }
    if system_fingerprint:
        result["system_fingerprint"] = system_fingerprint
    if last_usage:
        result["usage"] = last_usage
    return result

def clean_json_markdown(text: str) -> str:
    """
    Remove cercas de markdown (```json ... ``` ou ``` ... ```) se presentes,
    inclusive com preâmbulos, texto ao redor ou cercas sem fechamento estrito.
    """
    if not text:
        return ""
    cleaned = text.strip()

    # 1. Procura bloco fechado de markdown ```json ... ``` ou ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. Se começa com ```json ou ``` mas não foi fechado até o fim
    unclosed = re.search(r"```(?:json)?\s*([\s\S]+)$", cleaned, re.IGNORECASE)
    if unclosed:
        cand = unclosed.group(1).strip()
        if cand.endswith("```"):
            cand = cand[:-3].strip()
        return cand

    # 3. Se ainda houver cercas isoladas no início ou fim
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    if cleaned.endswith("```"):
        cleaned = re.sub(r"\s*```$", "", cleaned)

    return cleaned.strip()

def extract_first_json_object(text: str) -> Optional[Any]:
    """
    Tenta extrair o primeiro objeto ou array JSON válido do texto,
    removendo markdown e balanceando chaves/colchetes.
    """
    cleaned = clean_json_markdown(text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    for i, ch in enumerate(text):
        if ch in ('{', '['):
            closing = '}' if ch == '{' else ']'
            depth = 0
            in_str = False
            esc = False
            for j in range(i, len(text)):
                c = text[j]
                if esc:
                    esc = False
                    continue
                if c == '\\':
                    esc = True
                    continue
                if c == '"':
                    in_str = not in_str
                    continue
                if not in_str:
                    if c == ch:
                        depth += 1
                    elif c == closing:
                        depth -= 1
                        if depth == 0:
                            candidate = text[i:j+1]
                            try:
                                return json.loads(candidate)
                            except Exception:
                                break
    return None


def extract_numbered_list_items(text: str) -> List[str]:
    """Extrai itens de uma lista numerada (ex: 1. "query", 2. query)."""
    items = []
    lines = text.splitlines()
    for line in lines:
        m = re.match(r"^\s*\d+[\.\)]\s*(.*)$", line)
        if m:
            val = m.group(1).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1].strip()
            if val:
                items.append(val)
    return items


def build_object_from_numbered_list(text: str, schema: dict) -> dict:
    """
    Se pedir json_schema e vier lista de linhas numeradas,
    tenta montar o objeto pelas chaves do schema.
    """
    if not isinstance(schema, dict):
        return {}

    properties = schema.get("properties", {})
    if not properties:
        items = extract_numbered_list_items(text)
        return {"items": items} if items else {}

    numbered_items = extract_numbered_list_items(text)

    # Divide texto em seções por cabeçalhos markdown
    sections = {}
    current_sec = "default"
    sections[current_sec] = []

    for line in text.splitlines():
        sec_m = re.match(r"^\s*(?:#+|\*\*|__)(.*?)(?::|\*\*|__|$)", line)
        if sec_m and sec_m.group(1).strip() and not re.match(r"^\d+", sec_m.group(1).strip()):
            sec_name = sec_m.group(1).strip().lower()
            current_sec = sec_name
            if current_sec not in sections:
                sections[current_sec] = []
        else:
            sections[current_sec].append(line)

    lower_text = text.lower()
    has_temporal = False
    if "temporal" in lower_text:
        if "none detected" not in lower_text and "no temporal" not in lower_text and "none" not in lower_text:
            has_temporal = True

    obj = {}
    assigned_array = False

    for prop, prop_def in properties.items():
        prop_lower = prop.lower()
        p_type = prop_def.get("type")
        if isinstance(p_type, list):
            p_type_main = [t for t in p_type if t != "null"]
            p_type = p_type_main[0] if p_type_main else "null"

        if p_type == "array":
            matched_sec_items = []
            for sec_name, sec_lines in sections.items():
                if any(w in sec_name for w in (prop_lower, "quer", "item", "search", "alternat")):
                    sec_text = "\n".join(sec_lines)
                    sec_items = extract_numbered_list_items(sec_text)
                    if sec_items:
                        matched_sec_items.extend(sec_items)
            if matched_sec_items:
                obj[prop] = matched_sec_items
                assigned_array = True
            elif not assigned_array and numbered_items:
                obj[prop] = numbered_items
                assigned_array = True
            else:
                obj[prop] = []
        elif p_type == "boolean":
            if "temporal" in prop_lower:
                obj[prop] = has_temporal
            else:
                obj[prop] = False
        elif p_type == "object":
            sub_props = prop_def.get("properties", {})
            sub_obj = {}
            for sub_p, sub_p_def in sub_props.items():
                sub_t = sub_p_def.get("type")
                if isinstance(sub_t, list) and "null" in sub_t:
                    sub_obj[sub_p] = None
                elif sub_t == "string":
                    sub_obj[sub_p] = ""
                elif sub_t == "boolean":
                    sub_obj[sub_p] = False
                elif sub_t in ("number", "integer"):
                    sub_obj[sub_p] = 0
                elif sub_t == "array":
                    sub_obj[sub_p] = []
                else:
                    sub_obj[sub_p] = None
            obj[prop] = sub_obj
        elif p_type == "string":
            m_kv = re.search(rf"(?:{re.escape(prop)}|{re.escape(prop_lower)})\s*[:=]\s*([^\r\n]+)", text, re.IGNORECASE)
            if m_kv:
                obj[prop] = m_kv.group(1).strip().strip('"\'')
            else:
                obj[prop] = ""
        elif p_type in ("number", "integer"):
            obj[prop] = 0
        elif p_type == "null":
            obj[prop] = None
        else:
            obj[prop] = None

    return obj


def conform_object_to_schema(obj: dict, schema: dict) -> dict:
    """
    Garante que o objeto JSON contenha as propriedades esperadas pelo schema,
    fazendo fallback para aliases (ex: rewritten_queries -> queries) e
    preenchendo valores padrão para propriedades ausentes.
    """
    if not isinstance(obj, dict) or not isinstance(schema, dict):
        return obj

    properties = schema.get("properties", {})
    if not properties:
        return obj

    result = dict(obj)

    for prop, prop_def in properties.items():
        if prop in result:
            continue

        p_type = prop_def.get("type")
        if isinstance(p_type, list):
            p_type_main = [t for t in p_type if t != "null"]
            p_type = p_type_main[0] if p_type_main else "null"

        matched_key = None
        for k in result.keys():
            k_clean = k.lower().replace("_", "").replace("-", "")
            prop_clean = prop.lower().replace("_", "").replace("-", "")
            if k_clean == prop_clean or prop_clean in k_clean or k_clean in prop_clean:
                matched_key = k
                break

        if not matched_key and p_type == "array":
            for k, v in list(result.items()):
                if isinstance(v, list) and k not in properties:
                    matched_key = k
                    break

        if matched_key:
            result[prop] = result.pop(matched_key)
        else:
            if p_type == "array":
                result[prop] = []
            elif p_type == "boolean":
                result[prop] = False
            elif p_type == "object":
                sub_props = prop_def.get("properties", {})
                sub_obj = {}
                for sub_p, sub_p_def in sub_props.items():
                    sub_t = sub_p_def.get("type")
                    if isinstance(sub_t, list) and "null" in sub_t:
                        sub_obj[sub_p] = None
                    elif sub_t == "string":
                        sub_obj[sub_p] = ""
                    elif sub_t == "boolean":
                        sub_obj[sub_p] = False
                    elif sub_t in ("number", "integer"):
                        sub_obj[sub_p] = 0
                    elif sub_t == "array":
                        sub_obj[sub_p] = []
                    else:
                        sub_obj[sub_p] = None
                result[prop] = sub_obj
            elif p_type == "string":
                result[prop] = ""
            elif p_type in ("number", "integer"):
                result[prop] = 0
            elif p_type == "null":
                result[prop] = None
            else:
                result[prop] = None

    return result


DEFAULT_QUERY_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}},
        "hasTemporalQuery": {"type": "boolean"},
        "dateRange": {
            "type": "object",
            "properties": {
                "start": {"type": ["string", "null"]},
                "end": {"type": ["string", "null"]}
            }
        }
    },
    "required": ["queries", "hasTemporalQuery", "dateRange"]
}
QUERY_REWRITE_EXACT_KEYS = {
    "original_query",
    "alternative_queries",
    "rewritten_queries",
    "date_range",
    "temporal_info",
    "has_temporal_query",
}


def map_to_query_rewrite(parsed: dict) -> dict:
    """Mapeia um objeto retornado pelo modelo para o schema esperado pelo supermemory-server."""
    queries = []
    for k in ("alternative_queries", "rewritten_queries", "queries", "search_queries", "alternatives"):
        val = parsed.get(k)
        if isinstance(val, list):
            queries.extend([str(x) for x in val if x])
            break
    if not queries:
        for k, v in parsed.items():
            if isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v):
                queries.extend([str(x) for x in v])
                break

    orig = parsed.get("original_query") or parsed.get("query")
    if orig and isinstance(orig, str):
        if orig not in queries:
            queries.insert(0, orig)
    if not queries and orig:
        queries = [str(orig)]
    if not queries:
        queries = [""]

    raw_dr = parsed.get("date_range") or parsed.get("dateRange") or parsed.get("temporal_info")
    start = None
    end = None
    if isinstance(raw_dr, dict):
        start = raw_dr.get("start")
        end = raw_dr.get("end")

    has_temporal = parsed.get("hasTemporalQuery")
    if not isinstance(has_temporal, bool):
        has_temporal = bool(start or end)

    return {
        "queries": queries,
        "hasTemporalQuery": has_temporal,
        "dateRange": {"start": start, "end": end}
    }


def repair_json_content(content: str, schema: Optional[dict] = None, is_query_rewrite: bool = False) -> str:
    """
    Garante que content seja JSON puro ou devolve o texto sem cercas markdown.
    1. Tenta json.loads em content.strip() diretamente.
    2. Se falhar, tenta json.loads em cleaned (sem cercas).
    3. Se falhar, tenta extrair o primeiro objeto/array JSON.
    4. Conforma ao schema ou mapeia query rewrite se aplicável.
    5. Se houver schema ou for query rewrite com lista numerada, tenta montar o objeto.
    6. Se for query rewrite e tudo falhar, devolve DEFAULT_QUERY_REWRITE_SCHEMA.
    7. Se houver texto e não puder ser parseado como JSON, devolve cleaned (sem cercas) em vez de "{}".
    """
    if not content or not content.strip():
        if schema:
            return json.dumps(build_object_from_numbered_list("", schema), ensure_ascii=False)
        if is_query_rewrite:
            return json.dumps(DEFAULT_QUERY_REWRITE_SCHEMA, ensure_ascii=False)
        return "{}"

    stripped = content.strip()
    cleaned = clean_json_markdown(content)

    parsed = None
    try:
        parsed = json.loads(stripped)
    except Exception:
        try:
            parsed = json.loads(cleaned)
        except Exception:
            pass

    if parsed is None:
        parsed = extract_first_json_object(stripped)
    if parsed is None and cleaned != stripped:
        parsed = extract_first_json_object(cleaned)

    if isinstance(parsed, dict):
        if schema:
            parsed = conform_object_to_schema(parsed, schema)
            return json.dumps(parsed, ensure_ascii=False)
        else:
            is_qr = is_query_rewrite or bool(set(parsed.keys()) & QUERY_REWRITE_EXACT_KEYS)
            if is_qr:
                mapped = map_to_query_rewrite(parsed)
                return json.dumps(mapped, ensure_ascii=False)
            return json.dumps(parsed, ensure_ascii=False)
    elif parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)

    if schema:
        built = build_object_from_numbered_list(cleaned, schema) or build_object_from_numbered_list(stripped, schema)
        if built:
            return json.dumps(built, ensure_ascii=False)
    elif is_query_rewrite:
        items = extract_numbered_list_items(cleaned) or extract_numbered_list_items(stripped)
        if items:
            mapped = map_to_query_rewrite({"queries": items})
            return json.dumps(mapped, ensure_ascii=False)
        return json.dumps(DEFAULT_QUERY_REWRITE_SCHEMA, ensure_ascii=False)

    # Não conseguiu parsear como JSON nem montar schema: devolve o texto sem cercas markdown
    return cleaned if cleaned else "{}"


JSON_PHRASE_REGEX = re.compile(
    r"\b(output|respond|return|reply|generate|provide|gere|retorne|responda|devolva|envie|formate)\b[^\n.!?]*\bjson\b|"
    r"\b(in|as|em|no|como)\s+(formato\s+)?json\b|"
    r"\bjson\s+(puro|válido|valido|object|schema|mode)\b|"
    r"\b(valid|raw|strict|puro|válido|valido)\s+json\b",
    re.IGNORECASE
)

REWRITE_PHRASE_REGEX = re.compile(
    r"\brewrite\b[^\n.!?]*\b(queries|query|search)\b|\balternative\s+(search\s+)?queries\b",
    re.IGNORECASE
)


def inspect_request_for_json(body_json: dict) -> Tuple[bool, Optional[dict]]:
    """Detecta se a requisição não-stream exige JSON puro (response_format json, tools ou o prompt pedir JSON)."""
    if not isinstance(body_json, dict):
        return False, None
    if body_json.get("stream") is True:
        return False, None

    resp_fmt = body_json.get("response_format")
    tools = body_json.get("tools")
    tool_choice = body_json.get("tool_choice")
    messages = body_json.get("messages", [])

    wants_json = False
    schema_obj = None

    if resp_fmt:
        if isinstance(resp_fmt, dict):
            fmt_type = str(resp_fmt.get("type", "")).lower()
            if "json" in fmt_type:
                wants_json = True
                if fmt_type == "json_schema":
                    schema_obj = resp_fmt.get("json_schema", {}).get("schema")
        elif isinstance(resp_fmt, str) and "json" in resp_fmt.lower():
            wants_json = True

    if tools or tool_choice:
        wants_json = True
        if not schema_obj and isinstance(tools, list) and len(tools) > 0:
            fn = tools[0].get("function", {})
            schema_obj = fn.get("parameters")

    if not wants_json and isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "")).lower()
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            c_lower = content.lower()
            if role == "system":
                if re.search(r"\bjson\b", c_lower) or REWRITE_PHRASE_REGEX.search(c_lower):
                    wants_json = True
                    break
            else:
                if JSON_PHRASE_REGEX.search(c_lower) or REWRITE_PHRASE_REGEX.search(c_lower):
                    wants_json = True
                    break

    return wants_json, schema_obj



def add_system_json_instruction(body_json: dict, schema_obj: Optional[dict] = None) -> dict:
    """Acrescenta mensagem de sistema exigindo JSON puro conforme o schema."""
    sys_instruction = (
        "CRITICAL REQUIREMENT: Output ONLY a valid, raw JSON object matching the required schema. "
        "Do NOT include markdown formatting, markdown code blocks (```json ... ```), explanations, notes, or any other conversational text. "
        "The response MUST start strictly with '{' and end with '}'."
    )
    if schema_obj:
        sys_instruction += f"\nJSON Schema to follow strictly:\n{json.dumps(schema_obj, ensure_ascii=False)}"

    messages = body_json.setdefault("messages", [])
    if isinstance(messages, list):
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = f"{messages[0].get('content', '')}\n\n{sys_instruction}".strip()
        else:
            messages.insert(0, {"role": "system", "content": sys_instruction})
    return body_json


def repair_completion_response(resp_dict: dict, schema: Optional[dict] = None, is_query_rewrite: bool = False) -> dict:
    """Repara content em todas as choices do objeto chat.completion."""
    if not isinstance(resp_dict, dict):
        return resp_dict
    for choice in resp_dict.get("choices", []):
        msg = choice.get("message")
        if isinstance(msg, dict):
            msg.pop("reasoning", None)
            msg.pop("reasoning_content", None)
            if "content" in msg and isinstance(msg["content"], str):
                msg["content"] = repair_json_content(msg["content"], schema, is_query_rewrite=is_query_rewrite)
    return resp_dict

class LLMProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [llmproxy] {fmt % args}\n")
        sys.stderr.flush()

    def do_GET(self):
        self._handle_proxy()

    def do_POST(self):
        self._handle_proxy()

    def do_PUT(self):
        self._handle_proxy()

    def do_DELETE(self):
        self._handle_proxy()

    def do_HEAD(self):
        self._handle_proxy()

    def do_OPTIONS(self):
        self._handle_proxy()

    def _handle_proxy(self):
        # 1. Lê corpo da requisição
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""

        # 2. Detecta se o cliente pediu stream ou saída JSON estruturada
        client_wants_stream = False
        wants_json = False
        schema_obj = None
        is_query_rewrite_req = False
        if self.command == "POST" and ("/chat/completions" in self.path):
            try:
                body_json = json.loads(body_bytes.decode("utf-8"))
                if isinstance(body_json, dict):
                    for msg in body_json.get("messages", []):
                        m_role = str(msg.get("role", "")).lower()
                        m_content = msg.get("content", "")
                        if isinstance(m_content, str):
                            mc_lower = m_content.lower()
                            if m_role == "system" and ("rewrite" in mc_lower and ("search" in mc_lower or "queries" in mc_lower)):
                                is_query_rewrite_req = True
                                break
                            if REWRITE_PHRASE_REGEX.search(mc_lower):
                                is_query_rewrite_req = True
                                break

                    resp_fmt = body_json.get("response_format")
                    tools = body_json.get("tools")
                    rf_type = resp_fmt.get("type") if isinstance(resp_fmt, dict) else None
                    rf_schema = resp_fmt.get("json_schema", {}).get("schema") if isinstance(resp_fmt, dict) else None
                    rf_schema_keys = list(rf_schema.get("properties", {}).keys()) if isinstance(rf_schema, dict) else []
                    tools_keys = [t.get("function", {}).get("name") for t in tools] if isinstance(tools, list) else []
                    wants_json, schema_obj = inspect_request_for_json(body_json)
                    if is_query_rewrite_req:
                        wants_json = True
                    sys.stderr.write(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [llmproxy] POST /chat/completions: "
                        f"stream={body_json.get('stream')}, is_rewrite={is_query_rewrite_req}, wants_json={wants_json}, "
                        f"response_format_type={rf_type}, schema_keys={rf_schema_keys}, tools={tools_keys}\n"
                    )
                    sys.stderr.flush()

                    modified = False
                    model_str = str(body_json.get("model", "")).lower()
                    if "gpt-oss" in model_str and "reasoning_effort" not in body_json:
                        body_json["reasoning_effort"] = "high"
                        modified = True

                    if body_json.get("stream") is True:
                        client_wants_stream = True
                    else:
                        if wants_json:
                            body_json = add_system_json_instruction(body_json, schema_obj)
                            modified = True

                    if modified:
                        body_bytes = json.dumps(body_json, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                sys.stderr.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [llmproxy] Error inspecting request: {e}\n")
                sys.stderr.flush()
        target_url = f"{TARGET_BASE_URL}{self.path}"

        # 3. Prepara headers para upstream
        upstream_headers = {}
        target_parsed = urlparse(TARGET_BASE_URL)
        target_netloc = target_parsed.netloc

        hop_by_hop = {"host", "connection", "keep-alive", "proxy-authenticate",
                      "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
        for k, v in self.headers.items():
            if k.lower() not in hop_by_hop:
                upstream_headers[k] = v
        upstream_headers["Host"] = target_netloc
        if self.command in ("POST", "PUT", "PATCH"):
            upstream_headers["Content-Length"] = str(len(body_bytes))
        req = urllib.request.Request(
            target_url,
            data=body_bytes if self.command in ("POST", "PUT", "PATCH") else None,
            headers=upstream_headers,
            method=self.command,
        )

        try:
            resp = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            fallback_url = None
            fallback_host = None
            if "192.168.18.171:20128" in target_url:
                fallback_url = target_url.replace("192.168.18.171:20128", "thainan-9router_web_1:20128")
                fallback_host = "thainan-9router_web_1:20128"
            elif "thainan-9router_web_1:20128" in target_url:
                fallback_url = target_url.replace("thainan-9router_web_1:20128", "192.168.18.171:20128")
                fallback_host = "192.168.18.171:20128"

            if fallback_url:
                fallback_headers = dict(upstream_headers)
                fallback_headers["Host"] = fallback_host
                req_fb = urllib.request.Request(
                    fallback_url,
                    data=body_bytes if self.command in ("POST", "PUT", "PATCH") else None,
                    headers=fallback_headers,
                    method=self.command,
                )
                try:
                    resp = urllib.request.urlopen(req_fb, timeout=300)
                except urllib.error.HTTPError as e2:
                    resp = e2
                except Exception as e2:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    err_bytes = json.dumps({"error": f"llmproxy gateway error (and fallback): {str(e2)}"}).encode()
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.end_headers()
                    self.wfile.write(err_bytes)
                    return
            else:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                err_bytes = json.dumps({"error": f"llmproxy gateway error: {str(e)}"}).encode()
                self.send_header("Content-Length", str(len(err_bytes)))
                self.end_headers()
                self.wfile.write(err_bytes)
                return

        # 4. Caso o cliente queira stream, repassa diretamente
        if client_wants_stream:
            self.send_response(resp.code)
            for header, value in resp.headers.items():
                if header.lower() not in ("connection", "transfer-encoding"):
                    self.send_header(header, value)
            self.end_headers()
            try:
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except BrokenPipeError:
                pass
            return

        # 5. Cliente NÃO pediu stream: lê resposta inteira
        resp_body = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        resp_body_stripped = resp_body.strip()
        resp_clean = resp_body_stripped.replace(b"data: [DONE]", b"").strip()

        # Se o corpo for diretamente um objeto JSON chat.completion (comum mesmo com header text/event-stream)
        parsed_direct_json = None
        if resp_clean.startswith(b"{"):
            try:
                candidate = json.loads(resp_clean.decode("utf-8"))
                if isinstance(candidate, dict) and "choices" in candidate:
                    parsed_direct_json = candidate
            except Exception:
                pass

        if parsed_direct_json is not None:
            for choice in parsed_direct_json.get("choices", []):
                msg = choice.get("message")
                if isinstance(msg, dict):
                    msg.pop("reasoning", None)
                    msg.pop("reasoning_content", None)
            if wants_json:
                parsed_direct_json = repair_completion_response(parsed_direct_json, schema_obj, is_query_rewrite=is_query_rewrite_req)
            out_bytes = json.dumps(parsed_direct_json, ensure_ascii=False).encode("utf-8")

            self.send_response(200 if resp.code in (200, 201) else resp.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(out_bytes)))
            self.end_headers()
            self.wfile.write(out_bytes)
            return

        is_sse = ("text/event-stream" in content_type.lower()) or resp_body_stripped.startswith(b"data:")

        if is_sse:
            sse_text = resp_body.decode("utf-8", errors="replace")
            aggregated = aggregate_sse_chunks(sse_text)
            if wants_json:
                aggregated = repair_completion_response(aggregated, schema_obj, is_query_rewrite=is_query_rewrite_req)
            out_bytes = json.dumps(aggregated, ensure_ascii=False).encode("utf-8")

            self.send_response(200 if resp.code in (200, 201) else resp.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(out_bytes)))
            self.end_headers()
            self.wfile.write(out_bytes)
        else:
            # Resposta não-SSE: remove reasoning de message e se pediu JSON, higieniza e repara
            try:
                parsed_resp = json.loads(resp_body.decode("utf-8"))
                if isinstance(parsed_resp, dict) and "choices" in parsed_resp:
                    for choice in parsed_resp.get("choices", []):
                        msg = choice.get("message")
                        if isinstance(msg, dict):
                            msg.pop("reasoning", None)
                            msg.pop("reasoning_content", None)
                    if wants_json:
                        parsed_resp = repair_completion_response(parsed_resp, schema_obj, is_query_rewrite=is_query_rewrite_req)
                    resp_body = json.dumps(parsed_resp, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass
            self.send_response(resp.code)
            for header, value in resp.headers.items():
                if header.lower() not in ("connection", "content-length", "transfer-encoding"):
                    self.send_header(header, value)
            if wants_json:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

def run_server():
    server_address = (LISTEN_HOST, LISTEN_PORT)
    httpd = ThreadingHTTPServer(server_address, LLMProxyHandler)
    print(f"llmproxy iniciado em http://{LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_BASE_URL}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
