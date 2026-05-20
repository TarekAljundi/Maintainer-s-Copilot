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
Redactor runs at exactly 3 boundaries:
1. structlog processor pipeline (every log line)
2. Langfuse `mask` callback (every span send)
3. Memory service write path (every long-term memory insert)

## Tests
Brief-mandated. See `tests/unit/test_redaction.py`. Asserts at each of the 3 boundaries that a fake `ghp_…` token never appears unredacted.

## Bias
False-positives over false-negatives. A real secret leak is worse than a non-secret string redacted.

## LLM-paraphrased secrets
System prompt instructs LLM to never repeat strings that look like secrets, even paraphrased. Hard guard. Soft guard (redactor) catches verbatim repetitions.
