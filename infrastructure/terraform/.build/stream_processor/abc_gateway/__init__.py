"""Agent Budget Controller.

A financial authorization gateway for AI inference. Every governed request has
its worst-case cost reserved atomically from every applicable budget scope
before it is allowed to reach a provider, and reconciled against
provider-reported usage afterwards.

The guarantee, for every scope S:

    committed_S + reserved_S <= limit_S

held under concurrent traffic.
"""

__version__ = "1.0.0"
