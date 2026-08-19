"""In-memory budget store.

Not a stub: it implements the full repository protocol with the same
transaction semantics as the DynamoDB backend, and it is where the strongest
concurrency proof in the suite runs.
"""

from ._txn import MemoryStore
from .budget_repo import InMemoryBudgetRepository

__all__ = ["InMemoryBudgetRepository", "MemoryStore"]
