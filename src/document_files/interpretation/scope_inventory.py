"""Resource-bounded candidate inventory, independent of a model request's size."""

import json

VERSION = "document-files.scope-inventory.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 1024


def size(value):
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


class InventoryBudget:
    def __init__(self, payload, signature):
        self.used = size({"payload": payload, "targets": {}, "source": signature})
        self.base_fits = self.used <= MAX_BYTES
        self.discovered = self.retained = self.omitted = self.incomplete = 0

    def retain(self, handle, public, private):
        self.discovered += 1
        amount = size(public) + size({handle: private}) + 2
        if not self.base_fits or self.retained >= MAX_CANDIDATES or self.used + amount > MAX_BYTES:
            self.omitted += 1
            return False
        self.used += amount
        self.retained += 1
        self.incomplete += not public["contextComplete"]
        return True

    def finish(self, payload, targets, signature):
        # Containment and final serialization can add overhead after admission.
        self.used = size({"payload": payload, "targets": targets, "source": signature})
        limited = not self.base_fits or self.omitted > 0 or self.used > MAX_BYTES
        return {
            "version": VERSION,
            "status": "resource_limited"
            if limited
            else "incomplete_context"
            if self.incomplete
            else "complete"
            if self.retained
            else "no_candidates",
            "candidateCount": self.discovered if self.base_fits else None,
            "retainedCandidates": self.retained,
            "omittedCandidates": self.omitted if self.base_fits else None,
            "incompleteContexts": self.incomplete,
            # Candidate/context/mapping content; excludes this small diagnostic record.
            "contentBytes": self.used,
            "maxBytes": MAX_BYTES,
            "maxCandidates": MAX_CANDIDATES,
        }
