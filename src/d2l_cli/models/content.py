"""Read-only course content structure, without file retrieval."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentNode:
    id: str
    title: str
    parent_module_id: str | None
    kind: str
    children: tuple[ContentNode, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentTree:
    course_id: str
    modules: tuple[ContentNode, ...]
    node_count: int
    completeness: str = "unknown"
