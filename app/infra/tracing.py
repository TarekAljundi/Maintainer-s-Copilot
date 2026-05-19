"""TracingPort wrapping Langfuse v2.

Interface:
    @observe(as_type=...) decorator
    trace_id() -> str | None
    mask(obj) -> obj   # delegates to redactor before span send
"""
