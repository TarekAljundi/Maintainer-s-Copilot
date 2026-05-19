"""ChatbotService — agent loop, tool dispatch, mem inject, SSE event stream.

Interface:
    run_turn(conv_id, user_msg, user) -> AsyncIterator[Event]

Max 6 tool-call steps. Temp=0.2. Auto memory recall at turn start.
"""
