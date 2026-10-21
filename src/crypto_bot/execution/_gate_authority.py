"""Private capability used to keep Testnet order submission behind the execution gate.

This is an in-process safety guard against accidental bypass, not a security boundary
against a developer who deliberately edits or introspects the Python process.
"""
_ORDER_GATE_AUTHORITY = object()
