"""Stored empty worksheet cells, distinct from implicit gaps and merge coverage."""


class MergeCoverage:
    """Point lookup without expanding potentially huge merged rectangles.

    A balanced bounding-box tree avoids testing every range for every empty cell.
    It only answers whether a source coordinate is covered by a different anchor.
    """

    def __init__(self, ranges):
        rectangles = [
            (
                item["origin"]["row"],
                item["origin"]["col"],
                item["origin"]["row"] + item["row_span"] - 1,
                item["origin"]["col"] + item["col_span"] - 1,
            )
            for item in ranges
        ]
        self.root = self._build(rectangles)

    @classmethod
    def _build(cls, values):
        if not values:
            return None
        box = (
            min(v[0] for v in values),
            min(v[1] for v in values),
            max(v[2] for v in values),
            max(v[3] for v in values),
        )
        if len(values) <= 8:
            return box, values, None, None
        axis = 0 if box[2] - box[0] >= box[3] - box[1] else 1
        values.sort(key=lambda v: v[axis] + v[axis + 2])
        mid = len(values) // 2
        return box, None, cls._build(values[:mid]), cls._build(values[mid:])

    def covered(self, row, col):
        pending = [self.root] if self.root else []
        while pending:
            box, values, left, right = pending.pop()
            if not (box[0] <= row <= box[2] and box[1] <= col <= box[3]):
                continue
            if values is None:
                pending.extend((left, right))
            elif any(
                r0 <= row <= r1 and c0 <= col <= c1 and (row, col) != (r0, c0)
                for r0, c0, r1, c1 in values
            ):
                return True
        return False


def stored_empty_type(cell, namespace):
    """Only an explicitly empty XML value, never a failed formula or bad type."""
    kind = cell.get("t", "n")
    if kind not in {"n", "str", "inlineStr"}:
        return None
    if cell.find(f"{{{namespace}}}f") is not None:
        return None
    value = cell.find(f"{{{namespace}}}v")
    if value is not None and value.text:
        return None
    if any(t.text for t in cell.iter(f"{{{namespace}}}t")):
        return None
    return kind


def empty_typed_value(kind):
    if kind in {"str", "inlineStr"}:
        return {"kind": "string", "value": "", "raw": "", "rawType": kind}
    return {"kind": "blank", "raw": "", "rawType": kind, "observation": "stored_empty_cell"}
