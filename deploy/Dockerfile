# Sentinel agent + demo trainer image.  The agent layer is stdlib-only on the
# frozen core, so no third-party Python dependencies are installed here.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY rl_risk_replay ./rl_risk_replay
COPY sentinel_agent ./sentinel_agent
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "sentinel_agent", "watch", "--events", "/data/events.jsonl", "--host", "0.0.0.0"]
