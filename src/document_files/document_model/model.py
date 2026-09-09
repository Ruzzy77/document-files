"""Product-owned additive observations; native v1 nodes are never rewritten."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field

OBSERVATION_VERSION = "document-files.observation.v1"


class ObservationBudgetExceeded(ValueError):
    """The additive observation must be returned as partial, never silently truncated."""


@dataclass
class ObservationDocument:
    nodes: dict[str, dict] = field(default_factory=dict)
    bindings: dict[str, dict] = field(default_factory=dict)
    regions: list[dict] = field(default_factory=list)
    tables: dict[str, dict] = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"schemaVersion": OBSERVATION_VERSION, **asdict(self)}

    def node(self, node_id: str, text: str, *, role="span", locator=None, **metadata) -> str:
        if len(self.nodes) >= 100000:
            raise ObservationBudgetExceeded("observation node budget exceeded")
        if node_id in self.nodes:
            raise ValueError("observation node ID collision")
        self.nodes[node_id] = {
            "text": text,
            "semanticRole": role,
            "sourceStructure": deepcopy(locator or {}),
            **deepcopy(metadata),
        }
        return node_id

    def bind(self, source_ref: str, *, path="/text", start=None, end=None, **metadata) -> str:
        if len(self.bindings) >= 200000:
            raise ObservationBudgetExceeded("observation binding budget exceeded")
        if source_ref not in self.nodes:
            raise ValueError("binding source node is absent")
        if (start is None) != (end is None):
            raise ValueError("binding range requires both endpoints")
        if (
            path == "/text"
            and start is not None
            and not 0 <= start <= end <= len(self.nodes[source_ref]["text"])
        ):
            raise ValueError("binding exceeds observed text")
        binding_id = f"b{len(self.bindings) + 1}"
        self.bindings[binding_id] = {
            "sourceRef": source_ref,
            "path": path,
            "start": start,
            "end": end,
            **deepcopy(metadata),
        }
        return binding_id

    def issue(self, code: str, **details) -> None:
        item = {"code": code, **details}
        if item not in self.issues:
            self.issues.append(item)
