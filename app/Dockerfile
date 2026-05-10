FROM python:3.13-slim

RUN useradd --system --uid 1001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
