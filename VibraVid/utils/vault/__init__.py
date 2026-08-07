# 29.01.26

from .vault_1 import ExternalSupaDBVault, claudio_vault
from .vault_2 import lab_vault

__all__ = ["claudio_vault", "lab_vault", "build_named_vault"]


def build_named_vault(name: str, cfg: dict):
    return ExternalSupaDBVault(base_url=(cfg or {}).get("url", ""), token=(cfg or {}).get("token", ""), name=name)
