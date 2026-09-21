"""Turns a validated JoinSpec into the actual merge code - deterministic,
hand-written, never LLM-authored, exactly like chart_render.py's relationship
to chart_spec.py. Column/dataset names embedded via repr() (Python literal
syntax), never raw string interpolation, since they trace back to an LLM.
"""


def build_join_code(spec: dict, left_name: str, right_name: str) -> str:
    left_on, right_on, how = spec["left_on"], spec["right_on"], spec["how"]
    lines = [
        "import pandas as pd",
        "",
        f"left_df = datasets[{left_name!r}]",
        f"right_df = datasets[{right_name!r}]",
        f"df = left_df.merge(right_df, left_on={left_on!r}, right_on={right_on!r}, how={how!r})",
        'df.to_parquet(f"{ARTIFACTS_DIR}/result.parquet", index=False)',
        "result = "
        f"{{'rows_before': int(len(left_df)), 'rows_after': int(len(df)), "
        f"'left_on': {left_on!r}, 'right_on': {right_on!r}, 'how': {how!r}}}",
    ]
    return "\n".join(lines)
