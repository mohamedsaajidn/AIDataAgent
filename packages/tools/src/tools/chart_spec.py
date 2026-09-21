"""Validates and auto-corrects a chart request against the real schema.

The LLM only ever fills in a small structured spec (chart_type/x/y/agg/
filters) - it never writes plotting code itself for a supported chart type.
This module is the deterministic gate between "what the model asked for" and
"what's actually sensible to render," so a bad request gets redirected to
something sane instead of producing garbage (or being handed to the plotting
code as-is). Chart types outside SUPPORTED_CHART_TYPES are handled by the
freehand fallback in agent.graphs.qa, not here.
"""

SUPPORTED_CHART_TYPES = {"histogram", "bar", "line", "box", "pie", "donut", "scatter", "area", "violin"}
_NUMERIC_TYPES = {"continuous"}
_CATEGORICAL_TYPES = {"categorical", "boolean"}
_ALLOWED_FILTER_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in"}

# Matches the profiler's own categorical threshold (tools.profiler) - above
# this, a bar chart's x-axis stops being readable as individual bars.
HIGH_CARDINALITY_THRESHOLD = 20
# Even after redirecting to bar, an x-axis this wide needs top-N + "Other"
# bucketing to stay readable at all (see chart_render.py).
BAR_TOP_N = 15
# Pie/donut slices become unreadable far sooner than bar chart categories.
PIE_TOP_N = 8
# A scatter plot with many thousands of points is slow to render and mostly
# unreadable overplotting - sample down rather than plot every row.
SCATTER_MAX_POINTS = 5000

_CHART_TYPE_ALIASES = {
    "bar chart": "bar",
    "column": "bar",
    "column chart": "bar",
    "pie chart": "pie",
    "donut chart": "donut",
    "doughnut": "donut",
    "doughnut chart": "donut",
    "line chart": "line",
    "area chart": "area",
    "scatter plot": "scatter",
    "scatterplot": "scatter",
    "box plot": "box",
    "boxplot": "box",
    "violin plot": "violin",
}


def normalize_chart_type(chart_type: str | None) -> str | None:
    if not chart_type:
        return chart_type
    key = chart_type.strip().lower()
    return _CHART_TYPE_ALIASES.get(key, key)


def validate_chart_spec(spec: dict, columns: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """Returns (corrected_spec, notes). corrected_spec is None if the
    request can't be salvaged even with auto-correction - notes then explain
    why, so the caller can turn it into a clarifying question.

    Callers should route a chart_type outside SUPPORTED_CHART_TYPES to the
    freehand fallback before calling this - the check here is a defensive
    backstop, not the primary gate.
    """
    notes: list[str] = []
    chart_type = normalize_chart_type(spec.get("chart_type"))
    x = spec.get("x")
    y = spec.get("y")
    agg = spec.get("agg")
    filters = spec.get("filters") or []

    if chart_type not in SUPPORTED_CHART_TYPES:
        return None, [f"'{spec.get('chart_type')}' isn't a supported chart type here."]

    for name in (x, y):
        if name and name not in columns:
            return None, [f"Column '{name}' doesn't exist in this dataset."]

    filter_error = _validate_filters(filters, columns)
    if filter_error:
        return None, [filter_error]

    # An id column is a unique-per-row identifier by definition - it can
    # never be a meaningful grouping (bar/pie/donut) or ordering (line/area)
    # axis, no matter the dataset's size. Checked once, centrally, before
    # any type-specific logic - including line's redirect to bar - so it
    # can't be missed by a cascaded correction that skips re-validation.
    if x and chart_type in ("bar", "line", "area", "pie", "donut") and columns[x]["inferred_type"] == "id":
        return None, [
            f"'{x}' is a unique record identifier, not a category or measurement, "
            "so it can't be charted meaningfully - could you pick a different column to group by?"
        ]

    if chart_type == "histogram":
        return _validate_histogram(x, y, agg, filters, columns, notes)
    if chart_type == "bar":
        return _validate_bar(x, y, agg, filters, columns, notes)
    if chart_type in ("pie", "donut"):
        return _validate_pie(chart_type, x, y, agg, filters, columns, notes)
    if chart_type == "scatter":
        return _validate_scatter(x, y, filters, columns, notes)
    if chart_type in ("line", "area"):
        return _validate_line(chart_type, x, y, agg, filters, columns, notes)
    return _validate_box(chart_type, x, y, filters, columns, notes)


def _validate_filters(filters: list[dict], columns: dict[str, dict]) -> str | None:
    for f in filters:
        column = f.get("column")
        if column not in columns:
            return f"Filter references unknown column '{column}'."
        if f.get("op") not in _ALLOWED_FILTER_OPS:
            return f"Unsupported filter operator '{f.get('op')}'."
    return None


def _validate_histogram(x, y, agg, filters, columns, notes):
    if not x:
        return None, ["A histogram needs a numeric column to show the distribution of."]
    if columns[x]["inferred_type"] not in _NUMERIC_TYPES:
        notes.append(
            f"'{x}' isn't a continuous numeric column, so a histogram doesn't apply - "
            "showing a bar chart of value counts instead."
        )
        return {"chart_type": "bar", "x": x, "y": None, "agg": "count", "filters": filters}, notes
    return {"chart_type": "histogram", "x": x, "y": None, "agg": None, "filters": filters}, notes


def _validate_bar(x, y, agg, filters, columns, notes):
    if not x:
        return None, ["A bar chart needs a column to group by."]

    x_type = columns[x]["inferred_type"]
    x_distinct = columns[x]["distinct_count"]

    # Type-based, not cardinality-based: "continuous" means "measurement,"
    # not "category," even if the specific column happens to have few
    # distinct values in this dataset (e.g. only 3 rows) - a bar chart with
    # a raw measurement as its category axis is never the right chart.
    if x_type in _NUMERIC_TYPES:
        notes.append(f"'{x}' is a continuous numeric column, so a bar chart doesn't apply - showing a histogram instead.")
        return {"chart_type": "histogram", "x": x, "y": None, "agg": None, "filters": filters}, notes

    if y and columns[y]["inferred_type"] not in _NUMERIC_TYPES:
        return None, [f"'{y}' isn't numeric, so it can't be aggregated for a bar chart."]

    if x_distinct > HIGH_CARDINALITY_THRESHOLD:
        notes.append(
            f"'{x}' has {x_distinct} distinct values - showing only the top {BAR_TOP_N} "
            "(by count) grouped with an 'Other' bucket so the chart stays readable."
        )

    return {"chart_type": "bar", "x": x, "y": y, "agg": agg or ("sum" if y else "count"), "filters": filters}, notes


def _validate_pie(chart_type, x, y, agg, filters, columns, notes):
    if not x:
        return None, [f"A {chart_type} chart needs a column to group by."]

    x_type = columns[x]["inferred_type"]
    x_distinct = columns[x]["distinct_count"]

    if x_type in _NUMERIC_TYPES:
        notes.append(f"'{x}' is a continuous numeric column, so a {chart_type} chart doesn't apply - showing a histogram instead.")
        return {"chart_type": "histogram", "x": x, "y": None, "agg": None, "filters": filters}, notes

    if y and columns[y]["inferred_type"] not in _NUMERIC_TYPES:
        return None, [f"'{y}' isn't numeric, so it can't be aggregated for a {chart_type} chart."]

    if x_distinct > PIE_TOP_N:
        notes.append(
            f"'{x}' has {x_distinct} distinct values - a {chart_type} chart only stays readable with a "
            f"handful of slices, so showing the top {PIE_TOP_N} grouped with an 'Other' slice."
        )

    return {"chart_type": chart_type, "x": x, "y": y, "agg": agg or ("sum" if y else "count"), "filters": filters}, notes


def _validate_scatter(x, y, filters, columns, notes):
    if not x or not y:
        return None, ["A scatter plot needs two numeric columns - one for x, one for y."]

    x_numeric = columns[x]["inferred_type"] in _NUMERIC_TYPES
    y_numeric = columns[y]["inferred_type"] in _NUMERIC_TYPES

    if x_numeric and y_numeric:
        return {"chart_type": "scatter", "x": x, "y": y, "agg": None, "filters": filters}, notes

    # Exactly one side is a measurement and the other is a category - "how
    # does this measurement vary across categories" is a box plot, not a
    # scatter plot with one axis reduced to a few fixed horizontal lanes.
    if y_numeric and columns[x]["inferred_type"] in _CATEGORICAL_TYPES:
        notes.append(f"'{x}' isn't numeric, so showing a box plot of '{y}' by '{x}' instead of a scatter plot.")
        return {"chart_type": "box", "x": x, "y": y, "agg": None, "filters": filters}, notes
    if x_numeric and columns[y]["inferred_type"] in _CATEGORICAL_TYPES:
        notes.append(f"'{y}' isn't numeric, so showing a box plot of '{x}' by '{y}' instead of a scatter plot.")
        return {"chart_type": "box", "x": y, "y": x, "agg": None, "filters": filters}, notes

    return None, [f"A scatter plot needs two numeric columns - '{x}' and '{y}' don't fit that."]


def _validate_line(chart_type, x, y, agg, filters, columns, notes):
    if not x:
        return None, [f"A {chart_type} chart needs an x-axis column, usually a date."]

    x_type = columns[x]["inferred_type"]
    # A line/area implies inherent order - categorical/id/text/boolean
    # columns don't have that regardless of how many distinct values they
    # have (a 3-category column is just as unordered as a 300-category one).
    if x_type not in ("datetime", "continuous"):
        notes.append(f"'{x}' isn't a time or ordered numeric column, so showing a bar chart instead.")
        return {"chart_type": "bar", "x": x, "y": y, "agg": agg or "sum", "filters": filters}, notes

    if not y:
        return None, [f"A {chart_type} chart needs a numeric y-axis column to plot over time."]
    if columns[y]["inferred_type"] not in _NUMERIC_TYPES:
        return None, [f"'{y}' isn't numeric, so it can't be a {chart_type} chart's y-axis."]

    return {"chart_type": chart_type, "x": x, "y": y, "agg": agg or "sum", "filters": filters}, notes


def _validate_box(chart_type, x, y, filters, columns, notes):
    if not y:
        return None, [f"A {chart_type} plot needs a numeric column to show the distribution of."]
    if columns[y]["inferred_type"] not in _NUMERIC_TYPES:
        return None, [f"'{y}' isn't numeric, so its distribution can't be plotted."]

    if x and columns[x]["inferred_type"] not in _CATEGORICAL_TYPES:
        notes.append(f"Ignoring '{x}' as a grouping column since it isn't categorical.")
        x = None

    return {"chart_type": chart_type, "x": x, "y": y, "agg": None, "filters": filters}, notes
