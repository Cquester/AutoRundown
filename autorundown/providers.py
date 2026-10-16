"""AI providers: the one place that knows how each vendor's API is called.

query.py asks for "this schema, from this system prompt and this message";
this module sends it to Anthropic, Google Gemini, xAI (Grok) or DeepSeek and
hands back the parsed object (or None when the reply did not validate), the
tokens it cost and whether the model declined. Everything after that -- the
citation check, the other-vehicle check, the added parts -- is query.py's and
runs the same whatever answered.

- Anthropic goes through the official SDK, with exactly the request the app
  sent before providers existed (`messages.parse` + `output_format`; effort
  on the planner, adaptive thinking on the answer, where the model takes them).
- Gemini (REST generateContent, JSON schema output), xAI (OpenAI-compatible
  chat completions, strict `json_schema`) and DeepSeek (OpenAI-compatible,
  JSON mode: it takes no schema, so the schema goes into the system prompt)
  go over plain HTTPS with the standard library -- no new dependency in the
  MSI. Their replies are validated here against the same pydantic model;
  a reply that doesn't validate is None, which query.py turns into "no
  parsable answer", never a crash.

Base URLs can be pointed elsewhere with ANTHROPIC_BASE_URL (read by the SDK),
GEMINI_BASE_URL, XAI_BASE_URL and DEEPSEEK_BASE_URL: dev.py --fake-api points
all four at tools/fake_anthropic.py, so a real key typed into a test box goes
nowhere.

Prices are USD per million tokens, checked on PRICE_CHECKED against each
provider's own price page (`price_source`). A model whose price is not known
has price_in/price_out None: its questions are logged with cost NULL and the
UI says "cost not tracked" rather than showing a made-up number.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError

PRICE_CHECKED = "2026-10-16"


class NoApiKey(RuntimeError):
    pass


class ApiUnavailable(RuntimeError):
    """An API call failed for a reason the user can act on."""


# `adaptive`/`effort` (Anthropic only) track which request parameters each
# model accepts -- passing adaptive thinking or an effort setting to a model
# that predates them is a 400, not a silent no-op.
# `price_changes`: [(first UTC day, price_in, price_out)] announced on the
# price page; the latest one that has started applies.
# `peak`: DeepSeek bills half price off-peak; price_in/out are the peak price.
PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "short": "Anthropic",
        "key_url": "https://platform.claude.com/settings/keys",
        "key_hint": "sk-ant-…",
        "env_key": "ANTHROPIC_API_KEY",
        "price_source": "https://platform.claude.com/docs/en/about-claude/pricing",
        # Sonnet, not Opus (the user, 2026-10-16: Opus is overkill for a
        # lookup that points at a page): ~2/5 of Opus's price per question
        "default_model": "claude-sonnet-5",
        "models": {
            "claude-opus-5": {
                "label": "Opus 5 — most capable",
                "price_in": 5.0, "price_out": 25.0,
                "adaptive": True, "effort": True,
            },
            "claude-sonnet-5": {
                # $3/$15 until the 2026-10-16 check: the page now says the
                # $2/$10 launch price became the standard price
                "label": "Sonnet 5 — balanced",
                "price_in": 2.0, "price_out": 10.0,
                "adaptive": True, "effort": True,
            },
            "claude-haiku-4-5": {
                "label": "Haiku 4.5 — cheapest",
                "price_in": 1.0, "price_out": 5.0,
                "adaptive": False, "effort": False,
            },
        },
    },
    "gemini": {
        "label": "Google Gemini",
        "short": "Gemini",
        "key_url": "https://aistudio.google.com/apikey",
        "key_hint": "AIza…",
        "env_key": "GEMINI_API_KEY",
        "base_env": "GEMINI_BASE_URL",
        "base_url": "https://generativelanguage.googleapis.com",
        "price_source": "https://ai.google.dev/gemini-api/docs/pricing",
        "default_model": "gemini-3.8-flash",
        "models": {
            "gemini-4-argon": {
                "label": "Gemini 4 Argon — most capable (no free tier)",
                "price_in": 2.0, "price_out": 10.0,
                "price_changes": [("2026-12-01", 4.0, 20.0)],
            },
            "gemini-3.8-flash": {
                "label": "Gemini 3.8 Flash — balanced",
                "price_in": 0.75, "price_out": 3.75,
                "price_changes": [("2027-01-01", 1.5, 7.5)],
            },
            "gemini-3.5-flash-lite": {
                "label": "Gemini 3.5 Flash-Lite — cheapest",
                "price_in": 0.30, "price_out": 2.50,
            },
        },
    },
    "xai": {
        "label": "xAI (Grok)",
        "short": "xAI",
        "key_url": "https://console.x.ai/team/default/api-keys",
        "key_hint": "xai-…",
        "env_key": "XAI_API_KEY",
        "base_env": "XAI_BASE_URL",
        "base_url": "https://api.x.ai/v1",
        "price_source": "https://docs.x.ai/docs/models",
        "default_model": "grok-4.7",
        "models": {
            # prompts here stay far under the 200k-token tier where the price doubles
            "grok-4.7": {"label": "Grok 4.7 — most capable",
                         "price_in": 2.0, "price_out": 6.0},
            "grok-4.3": {"label": "Grok 4.3 — cheaper",
                         "price_in": 1.25, "price_out": 2.5},
        },
    },
    "deepseek": {
        "label": "DeepSeek",
        "short": "DeepSeek",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_hint": "sk-…",
        "env_key": "DEEPSEEK_API_KEY",
        "base_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com",
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
        "default_model": "deepseek-v4-pro",
        "models": {
            # peak price (cache miss); half of it off-peak. Cache hits and
            # Chinese public holidays are not modelled: both make the logged
            # cost a little high, never low.
            "deepseek-v4-pro": {"label": "DeepSeek V4 Pro — most capable",
                                "price_in": 1.32, "price_out": 3.96, "peak": True},
            "deepseek-flash": {"label": "DeepSeek Flash — cheapest",
                               "price_in": 0.30, "price_out": 1.20, "peak": True},
        },
    },
}
DEFAULT_PROVIDER = "anthropic"
# DeepSeek peak hours, UTC, Monday-Friday: 01:00-04:00 and 06:00-10:00
_DEEPSEEK_PEAK_HOURS = set(range(1, 4)) | set(range(6, 10))


def provider_of(model: str) -> str | None:
    for pid, p in PROVIDERS.items():
        if model in p["models"]:
            return pid
    return None


def model_spec(model: str) -> dict:
    pid = provider_of(model)
    if pid:
        return PROVIDERS[pid]["models"][model]
    anth = PROVIDERS[DEFAULT_PROVIDER]
    return anth["models"][anth["default_model"]]


def default_model(provider: str) -> str:
    return PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])["default_model"]


def price(model: str, when: datetime | None = None) -> tuple[float, float] | None:
    """(USD per M input, per M output) for a call made `when` (UTC), or None
    when this model's price is not known."""
    pid = provider_of(model)
    if not pid:
        return None
    spec = PROVIDERS[pid]["models"][model]
    if spec.get("price_in") is None or spec.get("price_out") is None:
        return None
    when = when or datetime.now(timezone.utc)
    p_in, p_out = spec["price_in"], spec["price_out"]
    day = when.strftime("%Y-%m-%d")
    for start, i, o in sorted(spec.get("price_changes", [])):
        if day >= start:
            p_in, p_out = i, o
    if spec.get("peak") and not (when.weekday() < 5 and when.hour in _DEEPSEEK_PEAK_HOURS):
        p_in, p_out = p_in / 2, p_out / 2
    return p_in, p_out


def cost(model: str, input_tokens: int, output_tokens: int,
         when: datetime | None = None) -> float | None:
    pr = price(model, when)
    if pr is None:
        return None
    return input_tokens / 1e6 * pr[0] + output_tokens / 1e6 * pr[1]


def model_list(provider: str) -> list[dict]:
    """For /api/models and Shop setup: the provider's models with prices
    (None = "cost not tracked")."""
    p = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
    out = []
    for mid in p["models"]:
        pr = price(mid)
        out.append({"id": mid, "label": p["models"][mid]["label"],
                    "provider": provider,
                    "price_in": round(pr[0], 4) if pr else None,
                    "price_out": round(pr[1], 4) if pr else None,
                    "price_checked": PRICE_CHECKED if pr else None,
                    "price_source": p["price_source"],
                    "offpeak_half": bool(p["models"][mid].get("peak"))})
    return out


def base_url(provider: str) -> str:
    p = PROVIDERS[provider]
    return (os.environ.get(p.get("base_env", "")) or p.get("base_url", "")).rstrip("/")


# -- models added in Shop setup -------------------------------------------------
#
# A provider's newer models, found with list_models and added by the shop
# (Config.extra_models). They join PROVIDERS[...]["models"] so everything
# that reads that table -- the menu, the checks on /api/ask, the spend log --
# treats them alike, with two differences: no price is known for them
# (logged as cost NULL, "cost not tracked"; never guessed), and an added
# Claude model is sent the plain request (no effort, no adaptive thinking:
# an older model refuses those with a 400, and the list does not say which
# a model takes). Prompts were tuned on the built-in models only.

def sync_added(extra: dict) -> None:
    """Make PROVIDERS hold exactly the added models in `extra`
    ({provider: [model id, ...]}), on top of the built-in ones."""
    for pid, p in PROVIDERS.items():
        want = [m for m in (extra or {}).get(pid) or [] if isinstance(m, str)]
        for mid in [m for m, spec in p["models"].items() if spec.get("added") and m not in want]:
            del p["models"][mid]
        for mid in want:
            if mid not in p["models"] and provider_of(mid) is None:
                p["models"][mid] = {"label": f"{mid} — added, cost not tracked",
                                    "price_in": None, "price_out": None, "added": True,
                                    "adaptive": False, "effort": False}


# what list_models leaves out: models that are not for answering text
_NOT_CHAT = re.compile(r"embed|image|imagine|vision|tts|audio|speech|live|realtime|"
                       r"transcri|moderation|aqa|veo|imagen|lyria|robotics|computer-use", re.I)


def list_models(provider: str, api_key: str) -> list[dict]:
    """The text models the provider says this key can use, [{id, label}],
    newest-looking first as the provider lists them. One GET, no tokens, no
    cost. Raises NoApiKey / ApiUnavailable like call()."""
    if not api_key:
        raise NoApiKey(no_key_message(provider))
    if provider == "anthropic":
        import anthropic
        try:
            page = anthropic.Anthropic(api_key=api_key).models.list(limit=100)
            rows = [(m.id, getattr(m, "display_name", "") or m.id) for m in page.data]
        except anthropic.APIError as exc:
            raise _friendly_anthropic_error(exc) from None
        keep = [(i, n) for i, n in rows if i.startswith("claude")]
    elif provider == "gemini":
        data = _get_json("gemini", f"{base_url('gemini')}/v1beta/models?pageSize=1000",
                         {"x-goog-api-key": api_key})
        keep = [(m["name"].removeprefix("models/"), m.get("displayName") or "")
                for m in data.get("models") or [] if isinstance(m, dict) and m.get("name")
                and "generateContent" in (m.get("supportedGenerationMethods") or [])]
        keep = [(i, n) for i, n in keep if i.startswith("gemini")]
    elif provider in ("xai", "deepseek"):
        data = _get_json(provider, f"{base_url(provider)}/models",
                         {"Authorization": f"Bearer {api_key}"})
        keep = [(m["id"], m["id"]) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]
    else:
        raise ApiUnavailable(f"Unknown AI provider {provider!r}.")
    seen, out = set(), []
    for mid, name in keep:
        if mid in seen or _NOT_CHAT.search(mid) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", mid):
            continue
        seen.add(mid)
        out.append({"id": mid, "label": name or mid})
    return out


# -- the call -----------------------------------------------------------------

@dataclass
class _Tokens:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Reply:
    """What a provider gave back. `parsed` is None when the reply did not
    validate against the schema (or was empty); `refusal` when the model or
    the provider's safety filter declined. `usage` has input/output tokens,
    as query.Usage.add expects."""
    parsed: BaseModel | None
    refusal: bool = False
    usage: _Tokens = field(default_factory=_Tokens)


def call(provider: str, api_key: str, model: str, kind: str, system: str,
         user: str, schema: type[BaseModel], example: dict | None = None) -> Reply:
    """One structured call. `kind` is "plan" or "answer" (token limits and,
    for Anthropic, effort / thinking). Raises NoApiKey / ApiUnavailable."""
    if not api_key:
        raise NoApiKey(no_key_message(provider))
    if provider == "anthropic":
        return _anthropic(api_key, model, kind, system, user, schema)
    if provider == "gemini":
        return _gemini(api_key, model, kind, system, user, schema)
    if provider in ("xai", "deepseek"):
        return _openai_compat(provider, api_key, model, kind, system, user, schema, example)
    raise ApiUnavailable(f"Unknown AI provider {provider!r}.")


def no_key_message(provider: str) -> str:
    p = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
    return (f"No {p['short']} API key configured. Add it in Shop setup > AI answers"
            + (" or export ANTHROPIC_API_KEY." if provider == "anthropic" else "."))


# -- Anthropic: the official SDK, the request shape of before ----------------

def _friendly_anthropic_error(exc: Exception) -> ApiUnavailable:
    """Turn SDK exceptions into something readable above a fender.

    Ordered most specific first; the balance case is special-cased because it
    is by far the most common first-run failure and the fix is not obvious
    (API credit is billed separately from a Claude subscription).
    """
    import anthropic
    if isinstance(exc, anthropic.AuthenticationError):
        return ApiUnavailable(
            "The API key was rejected. Check it in Settings, or create a new "
            "one at https://console.anthropic.com -> API Keys.")
    if isinstance(exc, anthropic.RateLimitError):
        return ApiUnavailable("Rate limited by the API. Wait a moment and retry.")
    if isinstance(exc, anthropic.BadRequestError):
        detail = str(exc)
        if "credit balance" in detail.lower():
            return ApiUnavailable(
                "Out of API credit. The key is valid, but the account has no "
                "API balance. Note that API credit is separate from a Claude "
                "Pro/Max subscription -- top up at "
                "https://console.anthropic.com/settings/billing.")
        return ApiUnavailable(f"The API rejected the request: {detail}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ApiUnavailable(
            "Could not reach the Anthropic API. Check the network connection.")
    if isinstance(exc, anthropic.APIStatusError):
        return ApiUnavailable(f"API error {exc.status_code}: {exc}")
    return ApiUnavailable(f"Unexpected API failure: {exc}")


def _anthropic(api_key, model, kind, system, user, schema) -> Reply:
    import anthropic
    spec = model_spec(model)
    client = anthropic.Anthropic(api_key=api_key)
    kwargs: dict = {}
    if kind == "plan":
        max_tokens = 1000
        if spec["effort"]:
            # planning is a light task; keep it cheap where the model allows it
            kwargs["output_config"] = {"effort": "low"}
    else:
        max_tokens = 8000
        if spec["adaptive"]:
            kwargs["thinking"] = {"type": "adaptive"}
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            **kwargs,
        )
    except anthropic.APIError as exc:
        raise _friendly_anthropic_error(exc) from None
    except (ValidationError, ValueError):
        # a reply that doesn't fit the schema: billed, but its token count is
        # lost with the response -- counted as a call, never a crash
        return Reply(parsed=None)
    return Reply(parsed=response.parsed_output,
                 refusal=response.stop_reason == "refusal",
                 usage=_Tokens(response.usage.input_tokens, response.usage.output_tokens))


# -- JSON schema for the other providers -------------------------------------

def _inline_refs(node, defs):
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_refs(defs[name], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _tidy(node):
    """No titles or defaults (Gemini's supported subset has no `default`),
    every property required and nothing extra -- what strict structured output
    expects; an empty list is how a model says "none"."""
    if isinstance(node, dict):
        out = {k: _tidy(v) for k, v in node.items() if k not in ("title", "default")}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_tidy(v) for v in node]
    return node


def json_schema(schema: type[BaseModel]) -> dict:
    raw = schema.model_json_schema()
    return _tidy(_inline_refs(raw, raw.get("$defs", {})))


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_reply(schema: type[BaseModel], text: str | None) -> BaseModel | None:
    """The reply validated against the schema, or None. Never raises."""
    if not text or not text.strip():
        return None
    try:
        return schema.model_validate_json(_FENCE_RE.sub("", text.strip()))
    except (ValidationError, ValueError):
        return None


# -- plain HTTPS ---------------------------------------------------------------

_RETRY_STATUS = {429, 500, 502, 503, 504, 529}
TIMEOUT = 300   # an answer with thinking can take a minute or two
RETRIES = 2


def _post_json(provider: str, url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    label = PROVIDERS[provider]["short"]
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if exc.code in _RETRY_STATUS and attempt < RETRIES:
                time.sleep(1 + attempt)
                continue
            raise _friendly_http_error(provider, exc.code, text) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
            if attempt < RETRIES:
                time.sleep(1 + attempt)
                continue
            raise ApiUnavailable(
                f"Could not reach the {label} API. Check the network connection.") from None
        except ValueError:
            raise ApiUnavailable(f"The {label} API sent a reply that is not JSON.") from None
    raise ApiUnavailable(f"The {label} API did not answer.")   # not reached


def _get_json(provider: str, url: str, headers: dict) -> dict:
    """A GET (the model list): no retries, a short wait -- the person is
    looking at Shop setup, not waiting on an answer."""
    label = PROVIDERS[provider]["short"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise _friendly_http_error(provider, exc.code, exc.read().decode("utf-8", "replace")) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        raise ApiUnavailable(f"Could not reach the {label} API. Check the network connection.") from None
    except ValueError:
        raise ApiUnavailable(f"The {label} API sent a reply that is not JSON.") from None


def _error_message(text: str) -> str:
    try:
        err = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(err, list) and err:
        err = err[0]
    e = err.get("error", err) if isinstance(err, dict) else err
    if isinstance(e, dict):
        return str(e.get("message") or e)[:300]
    return str(e)[:300]


def _friendly_http_error(provider: str, status: int, text: str) -> ApiUnavailable:
    p = PROVIDERS[provider]
    label, msg = p["short"], _error_message(text)
    low = msg.lower()
    if status == 429:
        if provider == "gemini":
            return ApiUnavailable(
                "Rate limited, or over the key's quota, by the Gemini API. Wait a "
                "moment and retry. Gemini's free tier allows only a limited number of "
                "requests a minute and a day; a key on a project with billing turned "
                f"on has higher limits. ({msg})")
        return ApiUnavailable(f"Rate limited by the {label} API. Wait a moment and retry.")
    if status == 402 or any(w in low for w in ("insufficient balance", "credit")):
        return ApiUnavailable(
            f"Out of API credit on the {label} account: the key is valid, but there "
            f"is no balance left. Top up in the {label} console ({p['key_url']}).")
    if status in (401, 403) or "api key not valid" in low or "invalid api key" in low:
        return ApiUnavailable(
            f"The {label} API key was rejected. Check it in Shop setup > AI answers, "
            f"or create a new one at {p['key_url']}.")
    if 400 <= status < 500:
        return ApiUnavailable(f"The {label} API rejected the request: {msg}")
    return ApiUnavailable(f"{label} API error {status}: {msg}")


# -- Gemini: REST generateContent ---------------------------------------------

_GEMINI_DECLINED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
                    "IMAGE_SAFETY"}


def _gemini(api_key, model, kind, system, user, schema) -> Reply:
    url = f"{base_url('gemini')}/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": json_schema(schema),
            # thinking counts against this on Gemini: generous, it bills only what is used
            "maxOutputTokens": 8000 if kind == "plan" else 32000,
        },
    }
    data = _post_json("gemini", url, {"x-goog-api-key": api_key}, body)
    meta = data.get("usageMetadata") or {}
    tokens = _Tokens(int(meta.get("promptTokenCount") or 0),
                     int(meta.get("candidatesTokenCount") or 0)
                     + int(meta.get("thoughtsTokenCount") or 0))
    if (data.get("promptFeedback") or {}).get("blockReason"):
        return Reply(parsed=None, refusal=True, usage=tokens)
    cands = data.get("candidates") or []
    if not cands:
        return Reply(parsed=None, usage=tokens)
    cand = cands[0]
    if cand.get("finishReason") in _GEMINI_DECLINED:
        return Reply(parsed=None, refusal=True, usage=tokens)
    text = "".join(part.get("text", "") for part in (cand.get("content") or {}).get("parts", [])
                   if not part.get("thought"))
    return Reply(parsed=parse_reply(schema, text), usage=tokens)


# -- xAI and DeepSeek: OpenAI-compatible chat completions ---------------------

JSON_MODE_NOTE = (
    "\n\nOutput format: reply with one JSON object and nothing else -- no prose "
    "before or after it, no code fences. It must follow this JSON schema:\n{schema}\n"
    "An example of the shape (the values are placeholders):\n{example}")


def _openai_compat(provider, api_key, model, kind, system, user, schema, example) -> Reply:
    url = f"{base_url(provider)}/chat/completions"
    sch = json_schema(schema)
    if provider == "xai":
        # strict structured output: the reply is constrained to the schema
        response_format = {"type": "json_schema",
                           "json_schema": {"name": schema.__name__, "schema": sch, "strict": True}}
    else:
        # DeepSeek's JSON mode takes no schema: it needs the word "json" and
        # an example in the prompt, and is validated here like the rest
        response_format = {"type": "json_object"}
        system = system + JSON_MODE_NOTE.format(
            schema=json.dumps(sch), example=json.dumps(example or {}))
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 8000 if kind == "plan" else 32000,
        "response_format": response_format,
    }
    data = _post_json(provider, url, {"Authorization": f"Bearer {api_key}"}, body)
    u = data.get("usage") or {}
    prompt = int(u.get("prompt_tokens") or 0)
    if u.get("total_tokens"):
        # xAI reports reasoning tokens beside completion tokens, DeepSeek
        # inside them; total - prompt is what was generated either way
        out = max(0, int(u["total_tokens"]) - prompt)
    else:
        out = (int(u.get("completion_tokens") or 0)
               + int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0))
    tokens = _Tokens(prompt, out)
    choices = data.get("choices") or []
    if not choices:
        return Reply(parsed=None, usage=tokens)
    msg = choices[0].get("message") or {}
    if msg.get("refusal") or choices[0].get("finish_reason") == "content_filter":
        return Reply(parsed=None, refusal=True, usage=tokens)
    return Reply(parsed=parse_reply(schema, msg.get("content")), usage=tokens)
