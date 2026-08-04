from __future__ import annotations

from provider_contracts import ConnectionType, ProviderAdapter, ProviderConnection


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, type[ProviderAdapter]] = {}

    def register(self, connection_type: str | ConnectionType, adapter_cls: type[ProviderAdapter]) -> None:
        key = str(connection_type.value if isinstance(connection_type, ConnectionType) else connection_type)
        if key in self._adapters:
            raise ValueError(f"provider_adapter_already_registered:{key}")
        self._adapters[key] = adapter_cls

    def create(self, connection: ProviderConnection) -> ProviderAdapter:
        cls = self._adapters.get(connection.connection_type)
        if cls is None:
            raise KeyError(f"unknown_connection_type:{connection.connection_type}")
        return cls(connection)

    def registered_types(self) -> list[str]:
        return sorted(self._adapters)


provider_registry = ProviderRegistry()
