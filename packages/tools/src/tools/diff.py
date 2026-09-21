"""Deterministic before/after comparison for a proposed transform or join.

Never asks the LLM to self-report what it changed - that's unreliable (it
could miscount or misdescribe). This compares the actual old schema (from
the catalog) against the actual new schema (read back from the produced
parquet), so the diff is always exactly what really happened.
"""


def compute_diff(old_columns: list[str], new_columns: list[str], old_row_count: int, new_row_count: int) -> dict:
    added = [c for c in new_columns if c not in old_columns]
    removed = [c for c in old_columns if c not in new_columns]

    parts = []
    if added:
        parts.append(f"added column(s): {', '.join(added)}")
    if removed:
        parts.append(f"removed column(s): {', '.join(removed)}")
    if not added and not removed:
        parts.append("no columns added or removed")

    if new_row_count != old_row_count:
        direction = "increased" if new_row_count > old_row_count else "decreased"
        parts.append(f"row count {direction} from {old_row_count} to {new_row_count}")
    else:
        parts.append(f"row count unchanged ({new_row_count} rows)")

    summary = "; ".join(parts)
    summary = summary[0].upper() + summary[1:] + "."

    return {
        "columns_added": added,
        "columns_removed": removed,
        "row_count_before": old_row_count,
        "row_count_after": new_row_count,
        "column_count_before": len(old_columns),
        "column_count_after": len(new_columns),
        "summary": summary,
    }
