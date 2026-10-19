FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RAYWORK_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY raywork ./raywork
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 studio && mkdir /data && chown studio:studio /data
USER studio
EXPOSE 8787
CMD ["raywork", "--host", "0.0.0.0", "--port", "8787"]
