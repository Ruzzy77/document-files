"""Distinguish source-declared headers from recognizer predictions."""

NATIVE_TABLE_BASES = {"native_structure", "native_html", "markdown_table_tokens"}


def declared_header(cell, table):
    # A recognizer's positive flag is still a prediction. Unknown origins must
    # not become declarations simply because they use the same boolean field.
    return cell.get("isHeader") is True and table.get("basis") in NATIVE_TABLE_BASES


def observed_rows(cells):
    rows = {}
    for cell in sorted(cells, key=lambda c: (c["row"], c["col"], c["sourceRef"])):
        for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1)):
            rows.setdefault(row, []).append(cell)
    return {
        row: sorted(observed, key=lambda c: (c["col"], c["row"], c["sourceRef"]))
        for row, observed in rows.items()
    }


def fixed_header_rows(table):
    # Mixed header/value rows remain decisions. Span geometry is observed, not
    # inferred from the order of source identifiers or from declared dimensions.
    return {
        row
        for row, cells in observed_rows(table["cells"]).items()
        if cells and all(declared_header(cell, table) for cell in cells)
    }
