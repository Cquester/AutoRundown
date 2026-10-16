# AI providers (Anthropic, Gemini, xAI, DeepSeek)

Added 2026-10-16 (branch `claude/ai-providers`, on `claude/accounts-signin`).
Switching provider is a setting (Shop setup → AI answers → *AI provider*);
the retrieval, the prompts and the safety checks are the same for all four.

## The one call

`autorundown/providers.py` is the only module that knows a vendor's API.
`query._ask` hands it `(kind, system, user, schema, example)` and gets back a
`Reply(parsed, refusal, usage)`; `parsed` is the pydantic object or None.
Everything after that is query.py's, unchanged and provider-blind:
`_verify_citations`, `_drop_other_vehicles`, `_add_omitted_parts`, the
withheld / not-found / declined paths, the spend log.

| Provider | Transport | Structured output | Key header |
|---|---|---|---|
| Anthropic | official SDK, `messages.parse(output_format=…)` | constrained (SDK) | `x-api-key` |
| Gemini | REST `v1beta/models/{m}:generateContent` | `responseMimeType: application/json` + `responseJsonSchema` | `x-goog-api-key` |
| xAI | OpenAI-compatible `/v1/chat/completions` | `response_format: json_schema`, `strict: true` | `Authorization: Bearer` |
| DeepSeek | OpenAI-compatible `/chat/completions` | JSON mode `json_object`; schema + example in the system prompt | `Authorization: Bearer` |

- **Anthropic's request is unchanged, byte for byte.** Checked by recording
  the request bodies and headers the SDK sent to the fake for all three
  Claude models before and after the refactor (6 requests, identical).
  `tests/test_providers.py` also checks the answer request has only
  `model, max_tokens, system, messages, output_config` (+ `thinking` where
  adaptive).
- **No new dependency.** The other three use `urllib.request` (stdlib), not
  `google-genai` / `openai`: smaller MSI, nothing to vendor, and the anthropic
  SDK's own HTTP client has changed under us before (1.0 ships `httpx2`).
- **Schemas** sent to Gemini/xAI/DeepSeek are pydantic's, with `$ref`s
  inlined, `title`/`default` dropped and every property required with
  `additionalProperties: false` (strict mode's rule; an empty list is how a
  model says "none"). Gemini's documented subset has no `default`.
- **Validation.** Every non-Anthropic reply goes through
  `providers.parse_reply` (`model_validate_json`, code fences tolerated);
  anything else -- prose, wrong types, truncated JSON, DeepSeek's known
  occasional empty content -- is None → "The model returned no parsable
  answer" (answer) or the raw question as the only query (planner). Never a
  crash; Anthropic's SDK parse errors are caught the same way.
- **Refusals**: Anthropic `stop_reason: refusal`; Gemini `promptFeedback.
  blockReason` or `finishReason` SAFETY / PROHIBITED_CONTENT / BLOCKLIST /
  SPII / RECITATION; OpenAI-compatible `message.refusal` or
  `finish_reason: content_filter` → "declined by the model".
- **Errors** become `ApiUnavailable` with the provider's name and key page;
  429/5xx/connection errors are retried twice (1 s, 2 s). A Gemini 429 says
  the free tier's limits are low. The key never appears in a message.
- **Token limits**: the non-Anthropic planner gets 8,000 output tokens and the
  answer 32,000 (thinking counts against them on Gemini; only what is used is
  billed). Anthropic keeps 1,000 / 8,000.
- **Output tokens** for the spend log: Gemini `candidatesTokenCount +
  thoughtsTokenCount`; OpenAI-compatible `total_tokens - prompt_tokens`
  (xAI reports reasoning beside completion tokens, DeepSeek inside them).

## Models and prices (checked 2026-10-16)

USD per million tokens, from each provider's price page
(`PROVIDERS[...]["price_source"]`); `providers.PRICE_CHECKED` is the date.

| Provider | Model | In / out | Notes |
|---|---|---|---|
| Anthropic | claude-opus-5 / sonnet-5 / haiku-4-5 | 5/25, 2/10, 1/5 | Sonnet 5 was 3/15 in this table; the page now says 2/10 is the standard price |
| Gemini | gemini-4-argon | 2/10, then 4/20 from 2026-12-01 | no free tier |
| Gemini | gemini-3.8-flash (default) | 0.75/3.75, then 1.50/7.50 from 2027-01-01 | free tier |
| Gemini | gemini-3.5-flash-lite | 0.30/2.50 | free tier |
| xAI | grok-4.7 (default) / grok-4.3 | 2/6, 1.25/2.50 | under 200k-token prompts (the price doubles above) |
| DeepSeek | deepseek-v4-pro (default) / deepseek-flash | 1.32/3.96, 0.30/1.20 peak | half off-peak |

- Announced price changes are stored as `price_changes` and applied by date.
- DeepSeek's peak hours are 01–04 and 06–10 UTC, Monday–Friday; outside them
  the price is halved. Chinese public holidays (off-peak) and cache hits
  (cheaper) are not modelled -- both make the logged cost a little high,
  never low.
- A model with `price_in`/`price_out` None is logged with `cost_usd` NULL
  and shown as "cost not tracked"; none is unknown today. Spend caps count
  only known costs, so an unpriced model would not count against a cap.
- `usage_log.provider` (new column, NULL = before = Anthropic) and
  `usage_log.model` record who answered; `ask.py --usage` prints both.

## Keys

`config.pick_key(cfg, user_id, admin, provider=None)` is still the one place
(see `shop-network.md`). Per provider: the Anthropic company key stays
`Config.api_key`; the others are `Config.provider_keys[provider]` (env
fallback `GEMINI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`). A mechanic's
Anthropic key stays at the top of `mechanic_keys["<id>"]`; the others sit
under `mechanic_keys["<id>"]["providers"][provider]` (`config.mechanic_entry`
/ `set_mechanic_entry`). `usage_log.key_ref` is "company" / "user:<id>" for
Anthropic, as before, and "company:gemini" / "user:<id>:gemini" for the
others, so each key's "spent since loaded" counts only its own spend. A
mechanic's key for one provider never pays for another. The key-per-mechanic
form edits the key of the active provider; switching provider keeps every
key. `POST /api/settings` takes `provider` and `api_key` + `key_provider`.

## "Get a key" links -- checked in a browser 2026-10-16

Opened with Python Playwright in a fresh, signed-out Edge (read only: no
sign-in, nothing clicked). Headless Edge was blocked by xAI's Cloudflare and
DeepSeek's CloudFront, so the record is from a visible window.

| Provider | Link | Lands on | Verdict |
|---|---|---|---|
| Anthropic | platform.claude.com/settings/keys | same URL, "API keys \| Claude Platform" (sign-in form) | correct (the old console.anthropic.com/settings/keys redirects here) |
| Gemini | aistudio.google.com/apikey | accounts.google.com sign-in, `continue=https://aistudio.google.com/apikey` | correct |
| xAI | console.x.ai/team/default/api-keys | console.x.ai/login?return_to=/team/default/api-keys | correct (the link xAI's quickstart uses) |
| DeepSeek | platform.deepseek.com/api_keys | same URL, "Human Verification" (bot check; not passed, by rule) | URL is the one DeepSeek's own docs link to; page past the check not seen |

Screenshots: `.dev/qa/claude-ai-providers/shots/link-headed-*.png` (not
committed). The Gemini free-tier note in Shop setup says what the pricing
page says (free tier for Flash / Flash-Lite, not Argon; free-tier content
"used to improve our products", paid not) and links to the page for limits.

## Testing (free)

`tools/fake_anthropic.py` fakes all four wire formats on one port
(`/v1/messages`, `/v1beta/models/<m>:generateContent`,
`/xai/v1/chat/completions`, `/deepseek/chat/completions`) and checks each
request the way that API would (key header; Gemini's schema subset; xAI's
strict json_schema; DeepSeek's "json" in the prompt). Every `fake:` word works
on every provider, plus `fake:malformed` and `fake:otherengine`.
`dev.py --fake-api` sets `fake_anthropic.base_urls(port)` --
`ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `XAI_BASE_URL`, `DEEPSEEK_BASE_URL`
-- so a key typed into a box goes only to the fake.
`tests/test_providers.py` (CI) runs Ask on each provider end to end;
`tests/test_epc_join.py` runs `_add_omitted_parts` and `_drop_other_vehicles`
on all four and compares the results with Anthropic's.

## Not verified

- **No real call to Gemini, xAI or DeepSeek has been made** (no keys, no
  spend). The request shapes follow each provider's docs as read on
  2026-10-16; the first paid question on each is the real test. Most likely
  to need a fix: Gemini's `responseJsonSchema` (Google's reference now marks
  it deprecated in favour of `responseFormat`, still documented and
  accepted per the docs), xAI's chat completions (labelled "legacy", no
  sunset date), and DeepSeek's JSON mode with its thinking mode (the docs
  don't say whether they combine; no thinking parameter is sent).
- The answer prompt was tuned on Claude. Other models may cite less or
  differently; the checks withhold what they can't verify, so the failure
  mode is "withheld / not found", not a wrong value.
