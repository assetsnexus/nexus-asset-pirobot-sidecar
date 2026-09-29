# Built by `docker compose up` in this directory. No registry image.
# Vendor tree under robot_control/adeept_rasptank2 is not modified — ANX overlay is anx-robot/.
FROM python:3.11-slim-bookworm

WORKDIR /app

# evdev builds a C extension. linux-libc-dev supplies linux/input.h and
# linux/input-event-codes.h. gcc and libc6-dev are required to compile it.
# Host kernel headers (linux-headers-$(uname -r)) are not in this image.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates gcc libc6-dev linux-libc-dev \
  && rm -rf /var/lib/apt/lists/*

# Upstream f5fe667 ships no requirements.txt. Flask + bridge deps live in the overlay.
COPY robot_control/anx-robot/requirements-bridge.txt /overlay/requirements-bridge.txt
RUN pip install --no-cache-dir --upgrade 'pip==26.2.1' \
  && pip install --no-cache-dir -r /overlay/requirements-bridge.txt

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
