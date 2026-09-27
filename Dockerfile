# Build context for eu1.dockerreg.sdk.assetsnexus.org/anx-robot-sidecar
# Publish pipeline builds this; do not docker build during example readiness checks.
# Vendor tree under robot_control/adeept_rasptank2 is not modified — ANX overlay is anx-robot/.
FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Upstream f5fe667 ships no requirements.txt. Flask + bridge deps live in the overlay.
COPY robot_control/anx-robot/requirements-bridge.txt /overlay/requirements-bridge.txt
RUN pip install --no-cache-dir -r /overlay/requirements-bridge.txt

COPY robot_control/adeept_rasptank2/web /app
COPY robot_control/anx-robot/anx_web_entry.py /overlay/anx_web_entry.py
COPY robot_control/anx-robot/anx_bridge /overlay/anx_bridge

ENV PYTHONUNBUFFERED=1
ENV ANX_ROBOT_WEB_DIR=/app
ENV PYTHONPATH=/overlay
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5)"

CMD ["python", "/overlay/anx_web_entry.py"]
