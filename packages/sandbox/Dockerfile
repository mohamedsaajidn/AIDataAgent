FROM python:3.12-slim

# Pinned to match the versions the rest of the workspace uses to write
# parquet files (see root uv.lock) - avoids cross-version pandas/pyarrow
# surprises when the sandbox reads data that Phase 1 produced.
RUN pip install --no-cache-dir \
    pandas==3.0.6 \
    numpy==2.4.6 \
    pyarrow==25.0.1 \
    duckdb==1.5.5 \
    scipy \
    scikit-learn \
    matplotlib \
    plotly

RUN useradd --create-home --uid 1000 sandbox

WORKDIR /app
COPY src/sandbox/runner.py /app/runner.py
RUN chown -R sandbox:sandbox /app

USER sandbox
ENV HOME=/tmp \
    MPLCONFIGDIR=/tmp/mplconfig \
    MPLBACKEND=Agg

CMD ["python", "/app/runner.py"]
