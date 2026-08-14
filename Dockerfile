FROM python:3.11-slim AS dahua-source

ARG DAHUA_CONSOLE_COMMIT=5711bc865e8831c2297ea19f719c69bdaa9e9fd3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && git init /opt/dahua \
    && git -C /opt/dahua remote add origin https://github.com/mcw0/DahuaConsole.git \
    && git -C /opt/dahua fetch --depth 1 origin "${DAHUA_CONSOLE_COMMIT}" \
    && git -C /opt/dahua checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/dahua rev-parse HEAD)" = "${DAHUA_CONSOLE_COMMIT}" \
    && rm -rf /opt/dahua/.git /var/lib/apt/lists/*


FROM python:3.11-slim

ARG VERSION=dev
ARG SOURCE_URL=https://github.com/gaming09/amcrest-ptz-bridge

LABEL org.opencontainers.image.title="Amcrest PTZ Bridge" \
      org.opencontainers.image.description="Local ONVIF-to-DVRIP pan/tilt bridge for Amcrest SmartHome cameras" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="MIT"

COPY --from=dahua-source /opt/dahua /opt/dahua

RUN pip install --no-cache-dir -r /opt/dahua/requirements.txt PyYAML==6.0.2 \
    && groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid bridge --no-create-home --home-dir /nonexistent bridge

WORKDIR /app
COPY bridge.py /app/bridge.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/dahua \
    LISTEN_PORT=18880

EXPOSE 18880/tcp

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; p=os.environ.get('LISTEN_PORT','18880'); urllib.request.urlopen('http://127.0.0.1:'+p+'/health',timeout=3).read()"

CMD ["python", "/app/bridge.py"]
