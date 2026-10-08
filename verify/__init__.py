"""Verifiers: the trust boundary of the app (CLAUDE.md "Verification rules").

Nothing here trusts an LLM's opinion. Each check compares a claimed answer to an independent
computation and returns a ``CheckResult``; ``dispatch.verify_question`` combines them into a
``core.schema.Verification`` record.
"""
