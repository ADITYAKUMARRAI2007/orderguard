"""Shop adapters: one interface, real stores behind it."""

from .base import (
    AdapterError,
    CartLine,
    CommerceAdapter,
    Location,
    ObservedCart,
    Offer,
    StoreUnavailable,
)
from .search import ScoredOffer, SearchOutcome, rank, search_stores
from .shopify_mcp import ShopifyMCPAdapter
from .stores import ALL, GENERAL, GROCERY, Store, by_domain
from ..cart_verifier import (
    ApprovedCartLine,
    CartComparison,
    CartExpectation,
    cart_hash,
    compare_cart,
)

__all__ = [
    "AdapterError", "CartLine", "CommerceAdapter", "Location", "ObservedCart", "Offer",
    "StoreUnavailable", "ShopifyMCPAdapter", "ScoredOffer", "SearchOutcome",
    "rank", "search_stores", "ALL", "GENERAL", "GROCERY", "Store", "by_domain",
    "ApprovedCartLine", "CartComparison", "CartExpectation", "cart_hash", "compare_cart",
]
