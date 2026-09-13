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


def row_role_order(table):
    """Observed rows not wholly declared as headers, not predicted data rows."""
    return sorted(set(observed_rows(table["cells"])) - fixed_header_rows(table))


def column_header(cell, table, roles):
    """Column definitions require compatible roles over the whole occupied span."""
    if cell.get("headerScope") in {"row", "rowgroup"}:
        return False
    occupied = [roles.get(row) for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1))]
    if all(role == "header" for role in occupied):
        return True
    # Declared header context outside a sliced view retains its declaration.
    # Native labels in mixed/content rows cannot become column headers by flag.
    return declared_header(cell, table) and all(role is None for role in occupied)
