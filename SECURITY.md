# Security

## Threat model
User-pasted text may contain secrets (API keys, JWTs, URL creds, emails). Without redaction these would land in 4 surfaces: structured logs, Langfuse trace spans, episodic memory summaries, audit-log metadata.

## Redaction patterns (defensible list)
- OpenAI key: `\bsk-[A-Za-z0-9]{32,}\b`
- Anthropic key: `\bsk-ant-[A-Za-z0-9_-]{90,}\b`
- Groq key: `\bgsk_[A-Za-z0-9]{50,}\b`
- GitHub token: `\bgh[pousr]_[A-Za-z0-9]{36,}\b`
- AWS access key: `\bAKIA[0-9A-Z]{16}\b`
- Google API: `\bAIza[0-9A-Za-z\-_]{35}\b`
- Slack: `\bxox[bpa]-[A-Za-z0-9-]{20,}\b`
- JWT shape: `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`
- URL creds: `://([^:\s/]+):([^@\s]+)@`
- Password key-value: `(?i)\b(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^\s"\']+)`
- Email: `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`
- Windows user path: `[Cc]:\\Users\\[^\\]+\\`
- Unix user path: `/Users/[^/]+/`, `/home/[^/]+/`

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
