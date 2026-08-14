"""Small, self-contained chat clients for Prompt Studio Video's local LLMs."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_KOBOLD_URL = "http://localhost:5001"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
AUTO_RESPONSE_FALLBACK_TOKENS = 4_096
AUTO_RESPONSE_CONTEXT_CAP_TOKENS = 12_288
MAX_RESPONSE_TOKENS = 131_072
DEFAULT_REQUEST_TIMEOUT = 600
MAX_REQUEST_TIMEOUT = 3_600


def _number(value, default, minimum, maximum, *, integer=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    result = max(minimum, min(maximum, result))
    return int(round(result)) if integer else result


def _allowed_hosts(environment_name):
    configured = os.environ.get(environment_name, "")
    values = {item.strip().casefold() for item in configured.split(",") if item.strip()}
    return values or set(DEFAULT_ALLOWED_HOSTS)


def clean_service_url(value, default, service_name, environment_name):
    cleaned = str(value or default).strip() or default
    if "://" not in cleaned:
        cleaned = "http://" + cleaned
    cleaned = cleaned.rstrip("/")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{service_name} URL must use http or https")
    if not parsed.hostname:
        raise ValueError(f"{service_name} URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{service_name} URL must not contain credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{service_name} URL contains an invalid port") from exc
    if parsed.query or parsed.fragment:
        raise ValueError(f"{service_name} URL must not contain a query string or fragment")

    hostname = parsed.hostname.casefold()
    allowed = _allowed_hosts(environment_name)
    if "*" not in allowed and hostname not in allowed:
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                f"{service_name} host '{parsed.hostname}' is not allowed. Add it to "
                f"{environment_name} or use '*' to allow remote hosts."
            )
    return cleaned


def _post_json(url, payload, timeout, service_name):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{service_name} request failed with HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"{service_name} did not return a response within {timeout} seconds"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {service_name} at {url}: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{service_name} returned invalid JSON: {body[:500]}") from exc


def _get_json(url, timeout):
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout, urllib.error.URLError, json.JSONDecodeError):
        return None


def generation_status(data):
    """Return a small, non-sensitive progress snapshot for a running local generation."""
    provider = str(data.get("llm_provider") or "koboldcpp").strip().casefold()
    if provider == "ollama":
        try:
            base_url = clean_service_url(
                data.get("ollama_url"),
                DEFAULT_OLLAMA_URL,
                "Ollama",
                "PROMPT_STUDIO_VIDEO_OLLAMA_ALLOWED_HOSTS",
            )
            result = _get_json(_ollama_url(base_url, "tags"), 3)
            if not isinstance(result, dict) or not isinstance(result.get("models"), list):
                return {"provider": provider, "reachable": False, "busy": None}
            models = {
                str(item.get("name") or item.get("model") or "").strip()
                for item in result["models"]
                if isinstance(item, dict)
            }
            selected_model = str(data.get("ollama_model") or "").strip()
            status = {
                "provider": provider,
                "reachable": True,
                "busy": None,
                "model": selected_model or None,
                "model_installed": selected_model in models if selected_model else None,
                "vision": None,
            }
            if not selected_model:
                status["message"] = "Ollama is online. Select a model to use it."
            elif selected_model not in models:
                status["message"] = f"Ollama is online, but '{selected_model}' is not installed."
            return status
        except (OverflowError, TypeError, ValueError):
            return {"provider": provider, "reachable": False, "busy": None}
    if provider != "koboldcpp":
        raise ValueError("llm_provider must be koboldcpp or ollama")
    try:
        base_url = clean_service_url(
            data.get("kobold_url"),
            DEFAULT_KOBOLD_URL,
            "KoboldCpp",
            "PROMPT_STUDIO_VIDEO_KOBOLD_ALLOWED_HOSTS",
        )
        perf = _get_json(urllib.parse.urljoin(base_url + "/", "api/extra/perf"), 3)
        if not isinstance(perf, dict):
            return {"provider": provider, "reachable": False, "busy": None}
        busy = perf.get("idle") == 0
        status = {
            "provider": provider,
            "reachable": True,
            "busy": busy,
            "queue": max(0, int(perf.get("queue") or 0)),
        }
        model_info = _get_json(
            urllib.parse.urljoin(base_url + "/", "api/v1/model"),
            3,
        )
        model_name = model_info.get("result") if isinstance(model_info, dict) else None
        status["model"] = (
            model_name.strip() if isinstance(model_name, str) and model_name.strip() else None
        )
        capabilities = _get_json(
            urllib.parse.urljoin(base_url + "/", "api/extra/version"),
            3,
        )
        vision = capabilities.get("vision") if isinstance(capabilities, dict) else None
        status["vision"] = vision if isinstance(vision, bool) else None
        if busy:
            try:
                partial = _post_json(
                    urllib.parse.urljoin(base_url + "/", "api/extra/generate/check"),
                    {},
                    3,
                    "KoboldCpp status check",
                )
                results = partial.get("results") if isinstance(partial, dict) else None
                first = results[0] if isinstance(results, list) and results else None
                text = str(first.get("text") or "") if isinstance(first, dict) else ""
                status["generated_characters"] = len(text) if isinstance(first, dict) else None
            except (RuntimeError, AttributeError, IndexError, KeyError, TypeError):
                status["generated_characters"] = None
        return status
    except (OverflowError, TypeError, ValueError):
        return {"provider": provider, "reachable": False, "busy": None}


def abort_generation(data):
    """Ask KoboldCpp to stop its currently active text generation."""
    base_url = clean_service_url(
        data.get("kobold_url"),
        DEFAULT_KOBOLD_URL,
        "KoboldCpp",
        "PROMPT_STUDIO_VIDEO_KOBOLD_ALLOWED_HOSTS",
    )
    result = _post_json(
        urllib.parse.urljoin(base_url + "/", "api/extra/abort"),
        {},
        10,
        "KoboldCpp abort",
    )
    return {
        "provider": "koboldcpp",
        "success": isinstance(result, dict) and result.get("success") is True,
    }


def _thinking_mode(value):
    mode = str(value or "Disabled").strip().casefold()
    return mode if mode in {"disabled", "minimal", "low", "medium", "high"} else "disabled"


def _ollama_url(base_url, endpoint):
    base = base_url.rstrip("/")
    if urllib.parse.urlsplit(base).path.rstrip("/").endswith("/api"):
        return f"{base}/{endpoint.strip('/')}"
    return f"{base}/api/{endpoint.strip('/')}"


def _messages_with_kobold_images(messages, images):
    output = [dict(message) for message in messages]
    if not images:
        return output
    user_index = next(
        (index for index in range(len(output) - 1, -1, -1) if output[index].get("role") == "user"),
        -1,
    )
    if user_index < 0:
        raise ValueError("Director vision request has no user message")
    text = output[user_index].get("content")
    output[user_index] = {
        **output[user_index],
        "content": [
            {"type": "text", "text": str(text or "")},
            *[
                {"type": "image_url", "image_url": {"url": image["data_uri"]}}
                for image in images
            ],
        ],
    }
    return output


def _messages_with_ollama_images(messages, images):
    output = [dict(message) for message in messages]
    if not images:
        return output
    user_index = next(
        (index for index in range(len(output) - 1, -1, -1) if output[index].get("role") == "user"),
        -1,
    )
    if user_index < 0:
        raise ValueError("Director vision request has no user message")
    output[user_index] = {
        **output[user_index],
        "images": [image["base64"] for image in images],
    }
    return output


def _kobold_chat(data, messages, images):
    base_url = clean_service_url(
        data.get("kobold_url"),
        DEFAULT_KOBOLD_URL,
        "KoboldCpp",
        "PROMPT_STUDIO_VIDEO_KOBOLD_ALLOWED_HOSTS",
    )
    timeout = _number(
        data.get("request_timeout"), DEFAULT_REQUEST_TIMEOUT, 5, MAX_REQUEST_TIMEOUT, integer=True
    )
    capabilities = _get_json(urllib.parse.urljoin(base_url + "/", "api/extra/version"), timeout)
    if isinstance(capabilities, dict) and capabilities.get("jinja") is False:
        raise RuntimeError(
            "KoboldCpp Chat Completions requires Use Jinja. Enable it and restart KoboldCpp."
        )
    if images and (not isinstance(capabilities, dict) or capabilities.get("vision") is not True):
        raise RuntimeError(
            "KoboldCpp vision is not active. Load a vision-capable model with its matching "
            "MMProj file, enable Use Jinja, and restart KoboldCpp."
        )
    messages = _messages_with_kobold_images(messages, images)
    mode = _thinking_mode(data.get("thinking_mode"))
    requested_tokens = _number(data.get("max_response_tokens"), 0, 0, MAX_RESPONSE_TOKENS, integer=True)
    automatic_tokens = requested_tokens == 0
    max_tokens = AUTO_RESPONSE_FALLBACK_TOKENS if automatic_tokens else requested_tokens
    context_info = _get_json(
        urllib.parse.urljoin(base_url + "/", "api/extra/true_max_context_length"),
        timeout,
    )
    try:
        context_length = int(context_info["value"]) if isinstance(context_info, dict) else None
    except (KeyError, TypeError, ValueError):
        context_length = None
    if context_length:
        try:
            token_count = _post_json(
                urllib.parse.urljoin(base_url + "/", "api/extra/tokencount"),
                {
                    "messages": messages,
                    "special": True,
                    "reasoning_effort": "none" if mode == "disabled" else mode,
                    "chat_template_kwargs": {"enable_thinking": mode != "disabled"},
                },
                timeout,
                "KoboldCpp",
            )
            prompt_tokens = int(token_count["value"])
        except (RuntimeError, KeyError, TypeError, ValueError):
            prompt_tokens = None
        if prompt_tokens is not None:
            available = context_length - prompt_tokens - 32
            if available < 64:
                raise RuntimeError(
                    f"The Director request uses {prompt_tokens} tokens and leaves too little room in "
                    f"KoboldCpp's {context_length}-token context. Reduce the Director context budget."
                )
            max_tokens = (
                min(available, AUTO_RESPONSE_CONTEXT_CAP_TOKENS)
                if automatic_tokens
                else min(requested_tokens, available)
            )
    payload = {
        "model": "koboldcpp",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": _number(data.get("temperature"), 0.7, 0, 5),
        "top_p": _number(data.get("top_p"), 0.9, 0, 1),
        "top_k": _number(data.get("top_k"), 100, 0, 200, integer=True),
        "min_p": _number(data.get("min_p"), 0, 0, 1),
        "rep_pen": _number(data.get("rep_pen"), 1.05, 0.5, 3),
        "rep_pen_range": _number(data.get("rep_pen_range"), 360, 0, 4096, integer=True),
        "seed": _number(data.get("sampler_seed"), -1, -1, 999999, integer=True),
        "reasoning_effort": "none" if mode == "disabled" else mode,
        "chat_template_kwargs": {"enable_thinking": mode != "disabled"},
        "encapsulate_thinking": True,
        "continue_assistant_turn": False,
        "stream": False,
    }
    result = _post_json(
        urllib.parse.urljoin(base_url + "/", "v1/chat/completions"),
        payload,
        timeout,
        "KoboldCpp",
    )
    try:
        choice = result["choices"][0]
        message = choice["message"]
        content = str(message.get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected KoboldCpp response: {result}") from exc
    if choice.get("finish_reason") == "length":
        budget = "available-context" if automatic_tokens else f"{max_tokens}-token"
        raise RuntimeError(f"KoboldCpp exhausted the {budget} Director response budget.")
    if not content.strip():
        raise RuntimeError("KoboldCpp returned an empty Director response")
    return content


def _ollama_chat(data, messages, images):
    base_url = clean_service_url(
        data.get("ollama_url"),
        DEFAULT_OLLAMA_URL,
        "Ollama",
        "PROMPT_STUDIO_VIDEO_OLLAMA_ALLOWED_HOSTS",
    )
    model = str(data.get("ollama_model") or "").strip()
    if not model:
        raise ValueError("Select an Ollama model in Director settings")
    timeout = _number(
        data.get("request_timeout"), DEFAULT_REQUEST_TIMEOUT, 5, MAX_REQUEST_TIMEOUT, integer=True
    )
    if images:
        model_info = _post_json(
            _ollama_url(base_url, "show"),
            {"model": model},
            timeout,
            "Ollama",
        )
        capabilities = model_info.get("capabilities") if isinstance(model_info, dict) else None
        capabilities = {str(value).casefold() for value in capabilities} if isinstance(capabilities, list) else set()
        if "vision" not in capabilities:
            raise RuntimeError(f"The selected Ollama model '{model}' does not advertise vision support.")
    messages = _messages_with_ollama_images(messages, images)
    mode = _thinking_mode(data.get("thinking_mode"))
    requested_tokens = _number(data.get("max_response_tokens"), 0, 0, MAX_RESPONSE_TOKENS, integer=True)
    automatic_tokens = requested_tokens == 0
    max_tokens = -2 if automatic_tokens else requested_tokens
    options = {
        "num_predict": max_tokens,
        "temperature": _number(data.get("temperature"), 0.7, 0, 5),
        "top_p": _number(data.get("top_p"), 0.9, 0, 1),
        "top_k": _number(data.get("top_k"), 100, 0, 200, integer=True),
        "min_p": _number(data.get("min_p"), 0, 0, 1),
        "repeat_penalty": _number(data.get("rep_pen"), 1.05, 0.5, 3),
        "repeat_last_n": _number(data.get("rep_pen_range"), 360, 0, 4096, integer=True),
    }
    seed = _number(data.get("sampler_seed"), -1, -1, 999999, integer=True)
    if seed >= 0:
        options["seed"] = seed
    result = _post_json(
        _ollama_url(base_url, "chat"),
        {
            "model": model,
            "messages": messages,
            "options": options,
            "think": False if mode == "disabled" else ("low" if mode in {"minimal", "low"} else mode),
            "stream": False,
        },
        timeout,
        "Ollama",
    )
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError(f"Ollama reported an error: {result.get('error') if isinstance(result, dict) else result}")
    content = str((result.get("message") or {}).get("content") or "")
    if result.get("done_reason") == "length":
        budget = "available-context" if automatic_tokens else f"{max_tokens}-token"
        raise RuntimeError(f"Ollama exhausted the {budget} Director response budget.")
    if not content.strip():
        raise RuntimeError("Ollama returned an empty Director response")
    return content


def generate_chat(data, messages, images=None):
    images = images or []
    provider = str(data.get("llm_provider") or "koboldcpp").strip().casefold()
    if provider == "koboldcpp":
        return _kobold_chat(data, messages, images)
    if provider == "ollama":
        return _ollama_chat(data, messages, images)
    raise ValueError("llm_provider must be koboldcpp or ollama")
