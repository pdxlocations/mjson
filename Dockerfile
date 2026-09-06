FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY mjson ./mjson
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home mjson
USER mjson
CMD ["python", "-m", "mjson"]
