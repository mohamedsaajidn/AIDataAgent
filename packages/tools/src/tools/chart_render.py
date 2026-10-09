"""Turns a validated ChartSpec into the actual plotting code that runs in the
sandbox. This is deterministic, hand-written, tested code - the LLM never
writes plotting code itself, only fills in the spec that chart_spec.py
validates. Column names and filter values are embedded via repr() (Python
literal syntax, not JSON - repr(None) is "None", json.dumps(None) is "null",
which is not valid Python and would silently produce a NameError at
execution time), never raw string interpolation, since they trace back to an LLM.
"""

from tools.chart_spec import BAR_TOP_N, PIE_TOP_N, SCATTER_MAX_POINTS

_FILTER_OPS = {
    "eq": "==",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

# "month"/"quarter" collapse to a year-qualified period ("2023-01", "2023Q1"),
# not the bare calendar month/quarter - across a multi-year dataset, "by
# month" almost always means the time series, not a seasonality view that
# combines every year's Januaries together. Values come out already sortable
# as plain strings (lexicographic order == chronological order here).
_DATE_PART_ACCESSORS = {
    "year": "dt.year",
    "month": "dt.to_period('M').astype(str)",
    "quarter": "dt.to_period('Q').astype(str)",
}


def build_chart_code(spec: dict, dataset_name: str) -> str:
    chart_type = spec["chart_type"]
    x, y, agg = spec.get("x"), spec.get("y"), spec.get("agg")
    filters = spec.get("filters") or []
    date_part = spec.get("date_part")
    color = spec.get("color")
    title = spec.get("title") or default_title(spec)

    lines = [
        "import pandas as pd",
        "import plotly.express as px",
        "",
        f"df = datasets[{dataset_name!r}]",
    ]
    for f in filters:
        lines.append(_render_filter(f))

    if date_part and x:
        # pd.to_datetime() is a defensive no-op when x is already a proper
        # datetime column (the normal case, post-ingestion) - cheap
        # insurance against a dataset predating that coercion.
        lines.append(f"df[{x!r}] = pd.to_datetime(df[{x!r}]).{_DATE_PART_ACCESSORS[date_part]}")

    if chart_type == "histogram":
        lines.append(f"fig = px.histogram(df, x={x!r}, title={title!r})")
    elif chart_type == "bar":
        lines.extend(_render_bar(x, y, agg, title, color))
    elif chart_type in ("pie", "donut"):
        lines.extend(_render_pie(chart_type, x, y, agg, title))
    elif chart_type == "scatter":
        lines.extend(_render_scatter(x, y, title))
    elif chart_type == "line":
        lines.extend(_render_line(x, y, agg, title, color))
    elif chart_type == "area":
        lines.extend(_render_area(x, y, agg, title, color))
    elif chart_type == "violin":
        lines.extend(_render_violin(x, y, title))
    else:  # box
        lines.extend(_render_box(x, y, title))

    # include_plotlyjs="cdn" would be far smaller, but the sandbox's installed
    # plotly package has a broken internal version constant
    # (plotly.offline.get_plotlyjs_version() -> "4.1.1") that "cdn" mode
    # trusts blindly to build the CDN URL - that pulls a real, genuinely old
    # plotly.js v4.1.1 from cdn.plot.ly that can't parse the compact
    # bdata/dtype typed-array encoding this plotly version's JSON output
    # uses, producing a blank chart with no error anywhere (confirmed: the
    # bundled plotly.min.js this package ships - used by True/inline mode -
    # DOES contain bdata-handling code, despite its own stale header comment
    # also claiming "v4.1.1"). Embedding inline is the only mode guaranteed
    # to match whatever this installed plotly actually produces.
    lines.append('fig.write_html(f"{ARTIFACTS_DIR}/chart.html", include_plotlyjs=True)')
    lines.append(
        "result = "
        f"{{'chart_type': {chart_type!r}, 'x': {x!r}, 'y': {y!r}, 'rows_plotted': int(len(df))}}"
    )
    return "\n".join(lines)


def _render_filter(f: dict) -> str:
    col = f["column"]
    if f["op"] == "in":
        return f"df = df[df[{col!r}].isin({f['value']!r})]"
    op = _FILTER_OPS[f["op"]]
    return f"df = df[df[{col!r}] {op} {f['value']!r}]"


def _render_bar(x: str, y: str | None, agg: str | None, title: str, color: str | None = None) -> list[str]:
    if not color:
        if not y or agg == "count":
            group_line = f"grouped = df.groupby({x!r}).size().reset_index(name='value')"
        else:
            group_line = f"grouped = df.groupby({x!r})[{y!r}].{agg or 'sum'}().reset_index(name='value')"

        return [
            group_line,
            "grouped = grouped.sort_values('value', ascending=False)",
            f"if len(grouped) > {BAR_TOP_N}:",
            # Cast to string before adding the "Other" row: x may be a
            # datetime (or other non-string) column, and mixing real
            # timestamps with the literal string "Other" in one column makes
            # Plotly misdetect the axis type in the browser - the chart
            # still "succeeds" (no Python error, a valid chart.html gets
            # written) but renders blank, since this happens client-side in
            # the browser's JS, not in this process.
            f"    grouped[{x!r}] = grouped[{x!r}].astype(str)",
            f"    top = grouped.iloc[:{BAR_TOP_N}].copy()",
            f"    other_total = grouped.iloc[{BAR_TOP_N}:]['value'].sum()",
            f"    other_row = pd.DataFrame([{{{x!r}: 'Other', 'value': other_total}}])",
            "    grouped = pd.concat([top, other_row], ignore_index=True)",
            f"fig = px.bar(grouped, x={x!r}, y='value', title={title!r})",
        ]

    # A split/grouped bar chart (color = a second categorical column, e.g.
    # "count by Geography, separated by whether they churned") - each x
    # value now has one row PER color sub-group, so top-N has to rank x by
    # its TOTAL count across all sub-groups first, then keep every
    # sub-group row for the surviving x values. There's no clean way to
    # decompose a synthetic "Other" bucket across an unknown number of
    # color sub-groups (unlike the single-dimension case above), so
    # anything past the top N is dropped outright rather than bucketed.
    if not y or agg == "count":
        group_line = f"grouped = df.groupby([{x!r}, {color!r}]).size().reset_index(name='value')"
    else:
        group_line = f"grouped = df.groupby([{x!r}, {color!r}])[{y!r}].{agg or 'sum'}().reset_index(name='value')"

    return [
        group_line,
        f"grouped[{x!r}] = grouped[{x!r}].astype(str)",
        # Cast color to string too - a 0/1 int flag (e.g. a boolean-typed
        # "Exited" column) passed to px.bar's color= untouched gets read as
        # a CONTINUOUS value by Plotly (a colorbar gradient, one blended
        # color per bar) rather than two distinct, legend-labeled groups -
        # the grouping itself (barmode='group') still works either way, but
        # the chart no longer visually reads as "split into categories" at
        # all. Casting forces Plotly's discrete/qualitative color path.
        f"grouped[{color!r}] = grouped[{color!r}].astype(str)",
        f"totals = grouped.groupby({x!r})['value'].sum().sort_values(ascending=False)",
        f"if len(totals) > {BAR_TOP_N}:",
        f"    keep = set(totals.index[:{BAR_TOP_N}].tolist())",
        f"    grouped = grouped[grouped[{x!r}].isin(keep)]",
        f"category_orders = {{{x!r}: totals.index[:{BAR_TOP_N}].tolist()}}",
        f"fig = px.bar(grouped, x={x!r}, y='value', color={color!r}, barmode='group', "
        f"category_orders=category_orders, title={title!r})",
    ]


def _render_pie(chart_type: str, x: str, y: str | None, agg: str | None, title: str) -> list[str]:
    hole = 0.45 if chart_type == "donut" else 0.0
    if not y or agg == "count":
        group_line = f"grouped = df.groupby({x!r}).size().reset_index(name='value')"
    else:
        group_line = f"grouped = df.groupby({x!r})[{y!r}].{agg or 'sum'}().reset_index(name='value')"

    return [
        group_line,
        "grouped = grouped.sort_values('value', ascending=False)",
        f"if len(grouped) > {PIE_TOP_N}:",
        # See _render_bar for why this cast happens before adding "Other".
        f"    grouped[{x!r}] = grouped[{x!r}].astype(str)",
        f"    top = grouped.iloc[:{PIE_TOP_N}].copy()",
        f"    other_total = grouped.iloc[{PIE_TOP_N}:]['value'].sum()",
        f"    other_row = pd.DataFrame([{{{x!r}: 'Other', 'value': other_total}}])",
        "    grouped = pd.concat([top, other_row], ignore_index=True)",
        f"fig = px.pie(grouped, names={x!r}, values='value', title={title!r}, hole={hole!r})",
    ]


def _render_scatter(x: str, y: str, title: str) -> list[str]:
    return [
        f"if len(df) > {SCATTER_MAX_POINTS}:",
        f"    df = df.sample(n={SCATTER_MAX_POINTS}, random_state=0)",
        f"fig = px.scatter(df, x={x!r}, y={y!r}, title={title!r})",
    ]


def _render_line(x: str, y: str, agg: str | None, title: str, color: str | None = None) -> list[str]:
    if not color:
        return [
            f"grouped = df.groupby({x!r})[{y!r}].{agg or 'sum'}().reset_index(name='value')",
            f"grouped = grouped.sort_values({x!r})",
            f"fig = px.line(grouped, x={x!r}, y='value', title={title!r})",
        ]
    return [
        f"grouped = df.groupby([{x!r}, {color!r}])[{y!r}].{agg or 'sum'}().reset_index(name='value')",
        # See _render_bar's identical cast for why - forces Plotly's
        # discrete/qualitative color path instead of a continuous gradient.
        f"grouped[{color!r}] = grouped[{color!r}].astype(str)",
        f"grouped = grouped.sort_values({x!r})",
        f"fig = px.line(grouped, x={x!r}, y='value', color={color!r}, title={title!r})",
    ]


def _render_area(x: str, y: str, agg: str | None, title: str, color: str | None = None) -> list[str]:
    if not color:
        return [
            f"grouped = df.groupby({x!r})[{y!r}].{agg or 'sum'}().reset_index(name='value')",
            f"grouped = grouped.sort_values({x!r})",
            f"fig = px.area(grouped, x={x!r}, y='value', title={title!r})",
        ]
    return [
        f"grouped = df.groupby([{x!r}, {color!r}])[{y!r}].{agg or 'sum'}().reset_index(name='value')",
        # See _render_bar's identical cast for why - forces Plotly's
        # discrete/qualitative color path instead of a continuous gradient.
        f"grouped[{color!r}] = grouped[{color!r}].astype(str)",
        f"grouped = grouped.sort_values({x!r})",
        f"fig = px.area(grouped, x={x!r}, y='value', color={color!r}, title={title!r})",
    ]


def _render_box(x: str | None, y: str, title: str) -> list[str]:
    if x:
        return [f"fig = px.box(df, x={x!r}, y={y!r}, title={title!r})"]
    return [f"fig = px.box(df, y={y!r}, title={title!r})"]


def _render_violin(x: str | None, y: str, title: str) -> list[str]:
    if x:
        return [f"fig = px.violin(df, x={x!r}, y={y!r}, box=True, title={title!r})"]
    return [f"fig = px.violin(df, y={y!r}, box=True, title={title!r})"]


def default_title(spec: dict) -> str:
    chart_type, x, y = spec["chart_type"], spec.get("x"), spec.get("y")
    date_part = spec.get("date_part")
    color = spec.get("color")
    x_label = f"{x} ({date_part})" if date_part else x
    color_suffix = f", split by {color}" if color else ""
    if chart_type == "histogram":
        return f"Distribution of {x}"
    if chart_type in ("bar", "pie", "donut"):
        return f"{y or 'Count'} by {x_label}{color_suffix}"
    if chart_type in ("line", "area"):
        return f"{y} over {x_label}{color_suffix}"
    if chart_type == "scatter":
        return f"{y} vs {x}"
    return f"Distribution of {y}" + (f" by {x}" if x else "")
