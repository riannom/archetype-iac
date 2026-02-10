"""Shared lazy singleton utilities for agent registries."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class LazySingleton(Generic[T]):
    """Create a singleton lazily from a factory function."""

    def __init__(self, factory: Callable[[], T]):
        self._factory = factory
        self._instance: T | None = None

    def get(self) -> T:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def reset(self) -> None:
        self._instance = None
