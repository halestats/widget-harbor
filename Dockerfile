FROM python:3.13-slim
LABEL org.opencontainers.image.title="Widget Harbor" \
      org.opencontainers.image.description="Self-hosted Forward and Rex widget library" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8765
WORKDIR /app
RUN groupadd --gid 10001 harbor && useradd --uid 10001 --gid harbor --no-create-home harbor \
    && mkdir /data && chown harbor:harbor /data
COPY --chown=harbor:harbor hub ./hub
COPY --chown=harbor:harbor static ./static
USER 10001:10001
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"
CMD ["python", "-m", "hub.server"]
