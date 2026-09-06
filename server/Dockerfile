FROM python:3.13-slim
ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="Budget Manager Server" \
      org.opencontainers.image.description="Self-hosted household budget and transaction review server" \
      org.opencontainers.image.version="${APP_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd -r budget && useradd -r -g budget -d /app budget
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x /app/entrypoint.sh /app/scripts/*.sh && chown -R budget:budget /app
USER budget
EXPOSE 8015
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn","config.wsgi:application","--bind","0.0.0.0:8015","--workers","2","--threads","4","--timeout","60","--access-logfile","-","--error-logfile","-"]
