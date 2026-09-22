"""Deterministic EDA report generation - no LLM involvement, since "profile
this dataset" has no ambiguity to resolve.

Deliberately NOT ydata-profiling: it hard-pins pandas<3.0, and this project
is pinned to pandas==3.0.6 for parquet compatibility across the whole
pipeline - forking the sandbox image onto a different pandas version just
for this one feature risks subtle behavioral differences everywhere else
that uses it (Q&A, charts, transforms). This report uses only pandas and
plotly, both already in the sandbox image, so it adds zero new dependencies.
Column/chart counts are capped so a wide dataset (this project has already
ingested one with 168 columns) still produces a report in reasonable time
and size, rather than growing without bound.
"""

_MAX_PROFILE_COLUMNS = 60
_MAX_CHART_COLUMNS = 6


def build_eda_code(dataset_name: str) -> str:
    return f"""import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

df = datasets[{dataset_name!r}]
n_rows, n_cols = len(df), len(df.columns)
n_dupes = int(df.duplicated().sum())

profile_cols = list(df.columns)[:{_MAX_PROFILE_COLUMNS}]
profile_rows = []
for col in profile_cols:
    s = df[col]
    non_null = s.dropna()
    null_pct = round(100 * (1 - len(non_null) / n_rows), 1) if n_rows else 0.0
    distinct = int(non_null.nunique())
    sample = ", ".join(str(v) for v in non_null.drop_duplicates().head(3))
    profile_rows.append(
        f"<tr><td>{{col}}</td><td>{{s.dtype}}</td><td>{{null_pct}}%</td>"
        f"<td>{{distinct}}</td><td>{{sample}}</td></tr>"
    )
profile_note = ""
if n_cols > {_MAX_PROFILE_COLUMNS}:
    profile_note = f"<p><em>Showing the first {_MAX_PROFILE_COLUMNS} of {{n_cols}} columns.</em></p>"
profile_table = (
    "<table><tr><th>Column</th><th>Type</th><th>Null %</th><th>Distinct</th><th>Sample values</th></tr>"
    + "".join(profile_rows) + "</table>"
)

numeric_cols = df.select_dtypes(include="number").columns.tolist()
describe_html = df[numeric_cols].describe().to_html() if numeric_cols else "<p>No numeric columns.</p>"

chart_numeric = numeric_cols[:{_MAX_CHART_COLUMNS}]
chart_categorical = [c for c in df.columns if c not in numeric_cols][:{_MAX_CHART_COLUMNS}]
chart_cols = chart_numeric + chart_categorical
charts_html = ""
if chart_cols:
    n_charts = len(chart_cols)
    n_rows_grid = (n_charts + 1) // 2
    fig = make_subplots(rows=n_rows_grid, cols=2, subplot_titles=chart_cols)
    r, c = 1, 1
    for col in chart_numeric:
        fig.add_trace(go.Histogram(x=df[col].dropna(), name=str(col)), row=r, col=c)
        c += 1
        if c > 2:
            c = 1
            r += 1
    for col in chart_categorical:
        vc = df[col].astype(str).value_counts().head(10)
        fig.add_trace(go.Bar(x=vc.index.tolist(), y=vc.values.tolist(), name=str(col)), row=r, col=c)
        c += 1
        if c > 2:
            c = 1
            r += 1
    fig.update_layout(height=300 * n_rows_grid, showlegend=False, title_text="Column Distributions")
    # include_plotlyjs=True (not "cdn"): see chart_render.py for why - this
    # sandbox's plotly package resolves "cdn" to a real but incompatible old
    # plotly.js build, producing a blank chart with no visible error.
    charts_html = fig.to_html(include_plotlyjs=True, full_html=False)

html = f\"\"\"<!doctype html>
<html><head><meta charset="utf-8"><style>
body {{{{ font-family: sans-serif; margin: 24px; }}}}
table {{{{ border-collapse: collapse; margin-bottom: 20px; }}}}
th, td {{{{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}}}
th {{{{ background: #f0f0f0; }}}}
</style></head><body>
<h1>EDA Report: {dataset_name}</h1>
<p>{{n_rows}} rows, {{n_cols}} columns, {{n_dupes}} duplicate row(s)</p>
<h2>Column Profile</h2>
{{profile_note}}
{{profile_table}}
<h2>Numeric Summary</h2>
{{describe_html}}
<h2>Distributions</h2>
{{charts_html}}
</body></html>\"\"\"

with open(f"{{ARTIFACTS_DIR}}/eda.html", "w", encoding="utf-8") as f:
    f.write(html)

result = {{"rows": int(n_rows), "columns": int(n_cols), "duplicate_rows": int(n_dupes)}}
"""
