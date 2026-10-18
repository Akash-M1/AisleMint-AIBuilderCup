"""Shared Gemini gateway with tenant-scoped caching and bounded generation/tool calls."""
from __future__ import annotations
import base64
from collections import OrderedDict
import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable
import httpx
from pydantic import BaseModel
from .brand import setting
from .cache import remember

DEFAULT_MODEL = 'gemini-3.8-flash'
FAST_MODEL = 'gemini-3.5-flash-lite'
FALLBACK_MODELS = ('gemini-3.8-flash', 'gemini-3.5-flash-lite', 'gemini-3-flash-preview')

_runtime_lock = threading.Lock()
_runtime_api_key: str = ''
_memory_cache: OrderedDict[str, Any] = OrderedDict()
MAX_MEMORY_CACHE = 256
CACHE_TTL_SECONDS = 900
MAX_OUTPUT_TOKENS = 2048
MAX_MODEL_ATTEMPTS = 2
MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS = 6
REQUEST_TIMEOUT_MS = 20000


def _clean_schema(schema: Any) -> Any:
    """Strip Pydantic metadata titles/defaults that strict JSON schema validators may reject."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in ('title', 'default', '$defs', 'additionalProperties'):
                continue
            out[k] = _clean_schema(v)
        return out
    if isinstance(schema, list):
        return [_clean_schema(x) for x in schema]
    return schema


def resolve_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    raw = model_cls.model_json_schema()
    defs = raw.get('$defs', {})

    def inline_refs(node: Any) -> Any:
        if isinstance(node, dict):
            if '$ref' in node:
                ref_name = node['$ref'].split('/')[-1]
                if ref_name in defs:
                    return inline_refs(defs[ref_name])
            return {
                k: inline_refs(v)
                for k, v in node.items()
                if k not in ('title', 'default', '$defs', 'additionalProperties')
            }
        if isinstance(node, list):
            return [inline_refs(x) for x in node]
        return node

    return inline_refs(raw)


def set_runtime_api_key(api_key: str) -> None:
    """Allow setting or updating the Gemini API key in-memory or persisting to .env."""
    global _runtime_api_key
    cleaned = (api_key or '').strip()
    with _runtime_lock:
        _runtime_api_key = cleaned
    if cleaned:
        os.environ['GEMINI_API_KEY'] = cleaned
        try:
            from .runtime import ENV_FILE
            if ENV_FILE and ENV_FILE.exists():
                lines = ENV_FILE.read_text().splitlines()
                updated = False
                new_lines = []
                for line in lines:
                    if line.strip().startswith(('GEMINI_API_KEY=', 'AISLEMINT_GEMINI_API_KEY=')):
                        if not updated:
                            new_lines.append(f'GEMINI_API_KEY={cleaned}')
                            updated = True
                    else:
                        new_lines.append(line)
                if not updated:
                    new_lines.append(f'GEMINI_API_KEY={cleaned}')
                ENV_FILE.write_text('\n'.join(new_lines) + '\n')
        except Exception as exc:
            logging.debug('Could not persist GEMINI_API_KEY to .env: %s', exc)


def get_api_key() -> str:
    with _runtime_lock:
        if _runtime_api_key:
            return _runtime_api_key
    for env_name in ('GEMINI_API_KEY', 'AISLEMINT_GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        val = os.environ.get(env_name, '').strip()
        if val:
            return val
    val = setting('GEMINI_API_KEY', '').strip()
    if val:
        return val
    try:
        from .runtime import ENV_FILE
        if ENV_FILE and ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() in ('GEMINI_API_KEY', 'AISLEMINT_GEMINI_API_KEY', 'GOOGLE_API_KEY'):
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
    except Exception:
        pass
    return ''


def ai_status() -> dict[str, Any]:
    key = get_api_key()
    sdk_installed = False
    try:
        from google import genai  # noqa: F401
        sdk_installed = True
    except Exception:
        pass
    return {
        'configured': bool(key),
        'key_preview': 'server managed' if key else None,
        'sdk': 'google-genai' if sdk_installed else 'httpx-interactions-rest',
        'primary_model': setting('GEMINI_MODEL', DEFAULT_MODEL) or DEFAULT_MODEL,
        'fast_model': FAST_MODEL,
        'cache_entries': len(_memory_cache),
        'limits': {'max_output_tokens': MAX_OUTPUT_TOKENS, 'model_attempts': MAX_MODEL_ATTEMPTS,
                   'tool_rounds': MAX_TOOL_ROUNDS, 'tool_calls': MAX_TOOL_CALLS},
    }


def _cache_get(cache_key: str) -> Any | None:
    with _runtime_lock:
        if cache_key in _memory_cache:
            expires, value = _memory_cache[cache_key]
            if time.monotonic() >= expires:
                del _memory_cache[cache_key]
                return None
            _memory_cache.move_to_end(cache_key)
            return value
    return None


def _cache_set(cache_key: str, value: Any) -> Any:
    with _runtime_lock:
        _memory_cache[cache_key] = (time.monotonic() + CACHE_TTL_SECONDS, value)
        _memory_cache.move_to_end(cache_key)
        while len(_memory_cache) > MAX_MEMORY_CACHE:
            _memory_cache.popitem(last=False)
    return value


_cached_client_key: str = ''
_cached_client: Any = None


def _get_genai_client(api_key: str) -> Any:
    global _cached_client_key, _cached_client
    with _runtime_lock:
        if _cached_client is not None and _cached_client_key == api_key:
            return _cached_client
        from google import genai
        from google.genai import types
        _cached_client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
        _cached_client_key = api_key
        return _cached_client


def _create_interaction(
    *,
    model: str,
    input_data: Any,
    system_instruction: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    previous_interaction_id: str | None = None,
    store: bool = False,
) -> dict[str, Any]:
    """Invoke Gemini via official google-genai Interactions API with low-latency thinking config."""
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY is not configured.')

    models_to_try = ([model] + [m for m in FALLBACK_MODELS if m != model])[:MAX_MODEL_ATTEMPTS]
    last_error: Exception | None = None

    for candidate_model in models_to_try:
        try:
            client = _get_genai_client(api_key)
            thinking_lvl = 'low' if '3.8' in candidate_model else 'minimal'
            kwargs: dict[str, Any] = {
                'model': candidate_model,
                'input': input_data,
                'store': store,
                'generation_config': {'thinking_level': thinking_lvl, 'max_output_tokens': MAX_OUTPUT_TOKENS},
                'timeout': REQUEST_TIMEOUT_MS / 1000,
            }
            if system_instruction:
                kwargs['system_instruction'] = system_instruction
            if tools:
                kwargs['tools'] = tools
            if response_format:
                kwargs['response_format'] = response_format
            if previous_interaction_id:
                kwargs['previous_interaction_id'] = previous_interaction_id
            if os.environ.get('AISLEMINT_HACKATHON') == '1':
                from .hackathon import claim_model_call
                claim_model_call()
            interaction = client.interactions.create(**kwargs)
            steps = []
            collected_texts = []
            for step in getattr(interaction, 'steps', []) or []:
                step_type = getattr(step, 'type', None)
                if step_type == 'function_call':
                    steps.append({
                        'type': 'function_call',
                        'id': getattr(step, 'id', ''),
                        'name': getattr(step, 'name', ''),
                        'arguments': getattr(step, 'arguments', {}) or {},
                    })
                elif step_type == 'model_output':
                    for part in getattr(step, 'content', []) or []:
                        txt = getattr(part, 'text', None)
                        if txt:
                            collected_texts.append(txt)
                    steps.append({
                        'type': 'model_output',
                        'text': getattr(interaction, 'output_text', '') or ''.join(collected_texts),
                    })
            out_txt = getattr(interaction, 'output_text', '') or ''.join(collected_texts)
            return {
                'id': getattr(interaction, 'id', ''),
                'model': candidate_model,
                'output_text': out_txt,
                'steps': steps,
            }
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f'Gemini API request failed: {last_error}')


def generate_text(
    prompt: str,
    *,
    system_instruction: str | None = None,
    model: str | None = None,
    workspace: str = 'benchmark',
    use_cache: bool = True,
) -> dict[str, Any]:
    chosen = model or setting('GEMINI_MODEL', DEFAULT_MODEL) or DEFAULT_MODEL
    digest = hashlib.sha256(
        json.dumps([workspace, chosen, system_instruction or '', prompt], sort_keys=True).encode()
    ).hexdigest()
    if use_cache:
        cached = _cache_get(f'text:{digest}')
        if cached is not None:
            return {**cached, 'cached': True}

    def _load():
        res = _create_interaction(
            model=chosen,
            input_data=prompt,
            system_instruction=system_instruction,
            store=False,
        )
        return {'text': (res.get('output_text') or '').strip(), 'model': res.get('model', chosen), 'cached': False}

    result = remember('gemini_text', workspace, digest, _load, ttl=900) if use_cache else _load()
    if use_cache:
        _cache_set(f'text:{digest}', result)
    return result


def generate_structured(
    prompt: str,
    schema_cls: type[BaseModel] | None = None,
    *,
    schema: type[BaseModel] | None = None,
    system_instruction: str | None = None,
    image_base64: str | None = None,
    image_mime_type: str = 'image/jpeg',
    model: str | None = None,
    workspace: str = 'benchmark',
    use_cache: bool = True,
    fallback: Any = None,
) -> tuple[Any, Any]:
    target_cls = schema_cls or schema
    if target_cls is None:
        raise ValueError('Provide schema or schema_cls to generate_structured.')
    api_key = get_api_key()
    if not api_key and fallback is not None:
        return fallback, False
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY is not configured.')

    chosen = model or setting('GEMINI_MODEL', DEFAULT_MODEL) or DEFAULT_MODEL
    resolved = resolve_schema(target_cls)
    img_hash = hashlib.sha256((image_base64 or '').encode()).hexdigest()[:16] if image_base64 else ''
    digest = hashlib.sha256(
        json.dumps([workspace, chosen, system_instruction or '', prompt, resolved, img_hash, image_mime_type], sort_keys=True).encode()
    ).hexdigest()
    if use_cache:
        cached = _cache_get(f'struct:{digest}')
        if cached is not None:
            parsed, used_model = cached
            return target_cls.model_validate(parsed), used_model

    clean_b64 = re.sub(r'^data:[^;]+;base64,', '', image_base64.strip()) if image_base64 else None

    # Fast path (~1.3s): Reuse warm genai.Client with generate_content + minimal/low thinking budget
    try:
        from google.genai import types
        client = _get_genai_client(api_key)
        fast_candidates = [FAST_MODEL, chosen] if chosen == DEFAULT_MODEL and not clean_b64 else [chosen, FAST_MODEL]
        for cand in list(dict.fromkeys(fast_candidates))[:MAX_MODEL_ATTEMPTS]:
            try:
                t_lvl = 'LOW' if '3.8' in cand else 'MINIMAL'
                contents: Any = (
                    [
                        types.Part.from_bytes(data=base64.b64decode(clean_b64), mime_type=image_mime_type),
                        prompt,
                    ]
                    if clean_b64
                    else prompt
                )
                if os.environ.get('AISLEMINT_HACKATHON') == '1':
                    from .hackathon import claim_model_call
                    claim_model_call()
                resp = client.models.generate_content(
                    model=cand,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type='application/json',
                        response_json_schema=resolved,
                        thinking_config=types.ThinkingConfig(thinking_level=t_lvl),
                        temperature=0.2,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    ),
                )
                raw_text = (resp.text or '').strip()
                if raw_text.startswith('```'):
                    raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text, flags=re.DOTALL).strip()
                validated = target_cls.model_validate_json(raw_text)
                if use_cache:
                    _cache_set(f'struct:{digest}', (validated.model_dump(), cand))
                return validated, cand
            except Exception:
                continue
    except Exception:
        pass

    # Do not multiply exhausted attempts through another API and more models.
    if fallback is not None:
        return fallback, False
    raise RuntimeError('Structured generation is unavailable or returned an invalid response.')


def _callable_to_tool_spec(fn: Callable[..., Any]) -> tuple[dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]:
    import inspect
    from typing import get_type_hints
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        ann = hints.get(name, param.annotation)
        json_type = 'number' if ann in (int, float) else ('boolean' if ann is bool else 'string')
        props[name] = {'type': json_type, 'description': f'Parameter {name}'}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    decl = {
        'type': 'function',
        'name': fn.__name__,
        'description': (fn.__doc__ or f'Execute {fn.__name__}').strip(),
        'parameters': {
            'type': 'object',
            'properties': props,
            'required': required,
        },
    }
    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return fn(**(args or {}))
    return decl, _handler


def run_agent_with_tools(
    prompt: str,
    *,
    system_instruction: str,
    tools: list[Callable[..., Any]] | None = None,
    tool_declarations: list[dict[str, Any]] | None = None,
    tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    model: str | None = None,
    max_rounds: int = 3,
    fallback_text: str = '',
) -> dict[str, Any]:
    """Execute a multi-step Gemini agent loop over deterministic Python tools."""
    decls = list(tool_declarations or [])
    handlers = dict(tool_handlers or {})
    for fn in tools or []:
        d, h = _callable_to_tool_spec(fn)
        decls.append(d)
        handlers[fn.__name__] = h

    chosen = model or setting('GEMINI_MODEL', DEFAULT_MODEL) or DEFAULT_MODEL
    if not get_api_key():
        return {
            'ai_powered': False,
            'answer': fallback_text,
            'model': 'deterministic-fallback',
            'tool_trace': [],
        }

    trace: list[dict[str, Any]] = []
    try:
        interaction = _create_interaction(
            model=chosen,
            input_data=prompt,
            system_instruction=system_instruction,
            tools=decls,
            store=True,
        )
        used_model = interaction.get('model', chosen)

        for _ in range(max(0, min(max_rounds, MAX_TOOL_ROUNDS))):
            fn_calls = [s for s in interaction.get('steps', []) if s.get('type') == 'function_call']
            if not fn_calls:
                break
            results_input = []
            for call in fn_calls:
                if len(trace) >= MAX_TOOL_CALLS:
                    raise RuntimeError('Tool call limit reached.')
                fn_name = call.get('name', '')
                fn_args = call.get('arguments') or {}
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}
                handler = handlers.get(fn_name)
                if handler is None:
                    tool_out = {'error': f'Unknown tool: {fn_name}'}
                else:
                    try:
                        tool_out = handler(fn_args)
                    except Exception as exc:
                        tool_out = {'error': str(exc)[:400]}
                trace.append({
                    'tool': fn_name,
                    'args': fn_args,
                    'arguments': fn_args,
                    'result': tool_out,
                })
                results_input.append({
                    'type': 'function_result',
                    'name': fn_name,
                    'call_id': call.get('id', ''),
                    'result': [{'type': 'text', 'text': json.dumps(tool_out, default=str)[:12000]}],
                })
            interaction = _create_interaction(
                model=used_model,
                input_data=results_input,
                system_instruction=system_instruction,
                tools=decls,
                previous_interaction_id=interaction.get('id') or None,
                store=True,
            )
            used_model = interaction.get('model', used_model)

        if any(s.get('type') == 'function_call' for s in interaction.get('steps', [])):
            raise RuntimeError('Tool round limit reached before a final answer.')
        if not (interaction.get('output_text') or '').strip():
            raise RuntimeError('No grounded final answer was generated.')

        return {
            'ai_powered': True,
            'answer': (interaction.get('output_text') or '').strip() or fallback_text,
            'model': used_model,
            'tool_trace': trace,
        }
    except Exception:
        return {
            'ai_powered': False,
            'answer': fallback_text,
            'model': 'deterministic-fallback',
            'tool_trace': trace,
        }
