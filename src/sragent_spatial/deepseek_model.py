"""DeepSeek (OpenAI-compatible) model factory for the pinned upstream SRAgent.

Upstream ``SRAgent.agents.utils.set_model`` dispatches on the model-name prefix
(``claude*`` -> ChatAnthropic, ``gpt-4*``/``o[0-9]*``/``gpt-5*`` -> FlexTierChatOpenAI,
otherwise a Google client). A ``deepseek-chat`` string therefore never reaches an
OpenAI-compatible client, and the upstream request body also carries
``service_tier``/``reasoning_effort`` fields that DeepSeek does not define.

Every upstream agent module binds the function at import time
(``from SRAgent.agents.utils import set_model``), so replacing that attribute on the
upstream module *before* importing any ``SRAgent.agents.*`` module reroutes the whole
hierarchy - including ``create_sragent_agent`` and the workflow graphs - without copying
or editing upstream source.

Nothing here imports SRAgent at module import time, so ``python run.py doctor`` keeps
working when the optional agent dependency is absent.
"""

from __future__ import annotations

import os
import re
import sys
import ssl
from contextlib import contextmanager
from contextvars import ContextVar

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TEMPERATURE = 0.1
MODEL_PATTERN = re.compile(r"deepseek[-_][A-Za-z0-9._/-]+")
_PATCH_FLAG = "_sragent_spatial_deepseek_patched"
_SCOPED_MODEL = ContextVar("sragent_spatial_model", default=None)


@contextmanager
def model_context(model):
    """Keep nested upstream factories on the explicitly selected model per task."""
    token = _SCOPED_MODEL.set(validate_model(model))
    try:
        yield
    finally:
        _SCOPED_MODEL.reset(token)


def resolve_model(model_name: str | None = None) -> str:
    """Explicit argument, then env override, then the documented default."""
    return (model_name or _SCOPED_MODEL.get() or os.getenv("SPATIAL_MODEL") or DEFAULT_MODEL).strip()


def validate_model(model_name: str | None) -> str:
    name = resolve_model(model_name)
    if not MODEL_PATTERN.fullmatch(name):
        raise ValueError(
            "DeepSeek provider expects a deepseek-* model id (e.g. deepseek-flash), "
            f"got {name!r}. Set SPATIAL_MODEL or pass --model explicitly."
        )
    return name


def base_url() -> str:
    return (os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY") or ""


def credentials_present() -> bool:
    return bool(api_key())


def thinking_enabled() -> bool:
    """DeepSeek thinking mode is on by default; we default it OFF for agent loops.

    With thinking enabled, the API emits ``reasoning_content`` on top of ``content`` and,
    for requests that carry ``tools``, requires that field to be passed back on every
    subsequent turn. LangChain/LangGraph does not round-trip ``reasoning_content``, so a
    multi-turn agent errors with 400 ``reasoning_content ... must be passed back``.
    Disabling thinking removes the field entirely. Opt back in with
    ``SPATIAL_DEEPSEEK_THINKING=enabled`` for single-shot, non-tool calls only.
    """
    return (os.getenv("SPATIAL_DEEPSEEK_THINKING") or "disabled").strip().lower() == "enabled"


def native_tls_context():
    """Use native roots directly, without reloading certifi for every nested client."""
    import truststore
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if os.getenv("SSL_CERT_FILE") or os.getenv("SSL_CERT_DIR"):
        context.load_verify_locations(cafile=os.getenv("SSL_CERT_FILE"), capath=os.getenv("SSL_CERT_DIR"))
    return context


def build(model_name=None, *, temperature=None, max_tokens=None, timeout=None, max_retries=2):
    """Create an OpenAI-compatible chat client pointed at DeepSeek.

    ``service_tier`` and ``reasoning_effort`` are deliberately not forwarded: they are
    OpenAI-specific request fields and are meaningless (or rejected) on this endpoint.
    """
    from langchain_openai import ChatOpenAI

    class DeepSeekChat(ChatOpenAI):
        def with_structured_output(self, schema=None, *, method="function_calling", **kwargs):
            # The upstream graph defaults to OpenAI's Chat Completions JSON Schema format.
            return super().with_structured_output(schema, method=method, **kwargs)

    key = api_key()
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is missing; provide it through the local environment, "
            "never in the repository or command arguments."
        )
    kwargs = {
        "model": validate_model(model_name),
        "api_key": key,
        "base_url": base_url(),
        "max_retries": max_retries,
    }
    if sys.platform == "win32":
        import httpx
        context = native_tls_context()
        kwargs["http_client"] = httpx.Client(verify=context)
        kwargs["http_async_client"] = httpx.AsyncClient(verify=context)
    if not thinking_enabled():
        # OpenAI-format toggle; see api-docs.deepseek.com/guides/thinking_mode
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    if timeout:
        kwargs["timeout"] = float(timeout)
    return DeepSeekChat(**kwargs)


def set_model(
    model_name=None,
    temperature=None,
    reasoning_effort=None,
    agent_name="default",
    max_tokens=None,
    service_tier=None,
):
    """Signature-compatible drop-in for ``SRAgent.agents.utils.set_model``.

    Upstream argument names are kept even when unused so that any call shape in the
    pinned upstream release keeps working.
    """
    del reasoning_effort, agent_name, service_tier  # DeepSeek has no equivalent fields
    return build(
        model_name,
        temperature=DEFAULT_TEMPERATURE if temperature is None else temperature,
        max_tokens=max_tokens,
    )


def is_installed() -> bool:
    upstream = sys.modules.get("SRAgent.agents.utils")
    return bool(upstream is not None and getattr(upstream, _PATCH_FLAG, False))


def imported_agent_modules() -> list[str]:
    return sorted(
        name for name in sys.modules
        if name.startswith("SRAgent.agents.") and name != "SRAgent.agents.utils"
    )


def install() -> dict:
    """Patch the upstream model factory. Must run before any ``SRAgent.agents.*`` import."""
    already = imported_agent_modules()
    if already:
        raise RuntimeError(
            "DeepSeek routing must be installed before upstream agent modules are imported; "
            "already imported: " + ", ".join(already)
        )
    import SRAgent.agents.utils as upstream

    upstream.set_model = set_model
    setattr(upstream, _PATCH_FLAG, True)
    return {"patched": "SRAgent.agents.utils.set_model", "base_url": base_url(),
            "model": resolve_model()}
