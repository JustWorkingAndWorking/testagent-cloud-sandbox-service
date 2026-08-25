"""
Registry HTTP API V2 集成层（v4 §7.4）。
"""

from __future__ import annotations

from infra.registry.client import RegistryClient, RegistryError

__all__ = [
    "RegistryClient",
    "RegistryError"
]
