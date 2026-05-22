# Security

## Threat model
User-pasted text may contain secrets (API keys, JWTs, URL creds, emails). Without redaction these would land in 4 surfaces: structured logs, Langfuse trace spans, episodic memory summaries, audit-log metadata.

## Redaction patterns (defensible list)

Authoritative source: `app/infra/redaction.py` (`PATTERNS`). Order is significant — URL credentials run first so the whole `scheme://user:pass@host` is replaced as one token before the inner password/email matchers fire.

Categories and replacement labels (`[REDACTED:<category>]`):

| Category | Regex | Notes |
|---|---|---|
| `url_credentials` | `\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@[^\s]+` | runs FIRST |
| `anthropic_key` | `\bsk-ant-[A-Za-z0-9_\-]{20,}\b` | before openai_key |
| `openai_key` | `\bsk-(?!ant-)[A-Za-z0-9_\-]{20,}\b` | negative lookahead avoids double-match |
| `groq_key` | `\bgsk_[A-Za-z0-9]{20,}\b` | |
| `github_token` | `\bgh[pousr]_[A-Za-z0-9]{20,}\b` | ghp/gho/ghu/ghs/ghr |
| `aws_access_key` | `\bAKIA[0-9A-Z]{16}\b` | |
| `google_api_key` | `\bAIza[0-9A-Za-z_\-]{35}\b` | |
| `slack_token` | `\bxox[bpa]-[A-Za-z0-9\-]{10,}\b` | |
| `jwt` | `\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b` | three base64url segments |
| `password_kv` | `(?i)\b(password|passwd|pwd)\s*[=:]\s*[^\s,;&]+` | |
| `email` | `\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b` | |
| `user_path` | `(?:[Cc]:\\Users\\|/Users/|/home/)[^\s\\/:*?"<>|]+` | Windows + macOS + Linux |

## NOT redacted (with rationale)
- IP addresses — issues legitimately discuss them, FP cost too high.
- Phone numbers — not in this corpus; pure FP risk.
- Names — too FP-prone, no clean regex.
- Generic high-entropy strings — would catch commit SHAs, UUIDs, hashes.

## Boundaries

Redactor runs at exactly 3 boundaries — every text path that leaves the
process for durable storage or display passes through one of them.

```
                ┌──────────────────────────────────────────────┐
  user input ───┤  app/services/chatbot.py  (in-process only)  │
                └──────────────────────────────────────────────┘
                       │                  │                 │
                       ▼                  ▼                 ▼
         ┌─────────────────────┐  ┌──────────────┐  ┌──────────────────┐
         │ structlog processor │  │ Langfuse     │  │ MemoryService    │
         │ (every log line)    │  │ mask cb      │  │ .write summary   │
         │                     │  │ (every span) │  │ (before embed!)  │
         │ redact() on `event` │  │ redact_obj() │  │ redact()         │
         │ + bind context      │  │ on inputs/   │  │ on summary       │
         └─────────┬───────────┘  │ outputs      │  └────────┬─────────┘
                   │              └──────┬───────┘           │
                   ▼                     ▼                   ▼
              stderr/JSON          Langfuse UI           Postgres
              log shipper          + retention             memories.summary
                                                          (+ embedding never
                                                           sees raw secret)
```

Three points worth highlighting:
- **Redaction happens BEFORE embedding** in the memory path so the vector
  cannot leak the secret either (an attacker with read access to the
  vector column couldn't reconstruct the original token via inversion).
- **The Langfuse `mask` callback uses the `data=` kwarg** in v2 (not
  positional) — see `app/infra/tracing.py`. Bit us once; it's load-bearing.
- **No 4th boundary needed** for HTTP responses — chatbot replies are
  in-process strings; the only persisted echo is via memory writes (#3).

## Tests

Brief-mandated. Two layers of coverage:

| Layer | File | What it asserts |
|---|---|---|
| Unit (per-pattern) | `tests/unit/test_redactor.py` | Each pattern category (vendor tokens, URL creds, JWT, email, user paths) redacts when present, leaves clean text alone. |
| Integration (per-boundary) | `tests/integration/test_redaction_boundaries.py` | At each of the 3 boundaries above: a `ghp_…` token in input never appears unredacted in the persisted/sent output. |

The integration test is the one PRD §Testing Decisions calls out as mandatory.

## Bias
False-positives over false-negatives. A real secret leak is worse than a non-secret string redacted.

## LLM-paraphrased secrets
System prompt instructs LLM to never repeat strings that look like secrets, even paraphrased. Hard guard. Soft guard (redactor) catches verbatim repetitions.

## Authentication & authorization

- **Streamlit is the admin console — admin-only.** `Home.py` signs in via
  `/auth/jwt/login`, then fetches `/api/me`; it stores the JWT in session
  state **only if `role == admin`**. A non-admin token is never persisted, so
  every Streamlit page (chat, memory inspector, widgets) is admin-gated at the
  single entry point.
- **Public registration cannot self-elevate.** `/auth/register` is exposed on
  the public demo host page. Its router binds a `UserRegister` schema with
  **no `role` field**, so a registrant always lands as `role=user`. The
  earlier schema accepted `role` — a self-elevation hole, closed once
  registration moved to a public surface. Admin is granted out of band
  (`UPDATE users SET role='admin'`).
- **Auth calls stay same-origin — no CORS widening.** The host page's
  sign-in / registration form reaches the api through an nginx proxy
  (`/auth/*`, `/api/me`). The api's dynamic CORS allowlist still covers only
  `/widget/*` and `/api/chat`, sourced per-widget from the database.
