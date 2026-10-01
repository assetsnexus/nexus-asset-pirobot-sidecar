# Built by `docker compose up` in this directory. No registry image.
# Vendor tree under robot_control/adeept_rasptank2 is not modified — ANX overlay is anx-robot/.
FROM python:3.11-slim-bookworm

WORKDIR /app

# evdev builds a C extension. linux-libc-dev supplies linux/input.h and
# linux/input-event-codes.h. gcc and libc6-dev compile it.
# liblgpio-dev / picamera2 / libcamera come from the Raspberry Pi archive.
# raspi-utils-core provides pinctrl, used to take GPIO 9 and 11 back from SPI.
# Host kernel headers (linux-headers-$(uname -r)) are not in this image.
#
# Adeept web/app.py imports camera_opencv at load time (cv2, picamera2, numpy).
# Without those packages the overlay falls back to static UI + /health and video fails.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
  && curl -fsSL https://archive.raspberrypi.org/debian/raspberrypi.gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.org/debian/ bookworm main" \
    > /etc/apt/sources.list.d/raspi.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    gcc libc6-dev linux-libc-dev swig \
    liblgpio1 liblgpio-dev libgpiod2 raspi-utils-core \
    python3-opencv python3-numpy python3-picamera2 python3-libcamera \
    python3-kms++ python3-prctl libcap2 libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

# System Bookworm packages install into dist-packages; this image's Python is also 3.11.
ENV PYTHONPATH=/usr/lib/python3/dist-packages:/overlay

# Upstream f5fe667 ships no requirements.txt. Flask + bridge deps live in the overlay.
COPY robot_control/anx-robot/requirements-bridge.txt /overlay/requirements-bridge.txt
RUN pip install --no-cache-dir --upgrade 'pip==26.2.1' \
  && pip install --no-cache-dir -r /overlay/requirements-bridge.txt

COPY robot_control/adeept_rasptank2/web /app
COPY robot_control/anx-robot/anx_web_entry.py /overlay/anx_web_entry.py
COPY robot_control/anx-robot/anx_tls.py /overlay/anx_tls.py
COPY robot_control/anx-robot/anx_bridge /overlay/anx_bridge

ENV PYTHONUNBUFFERED=1
ENV ANX_ROBOT_WEB_DIR=/app
ENV ANX_ROBOT_TLS=true
ENV ANX_ROBOT_TLS_CERT=/certs/tls/robot.crt
ENV ANX_ROBOT_TLS_KEY=/certs/tls/robot.key
EXPOSE 5000 8888

# /health is HTTPS when ANX_ROBOT_TLS=true (default). Never probe plain HTTP in that mode.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=40s \
  CMD python -c "import os,ssl,urllib.request; tls=os.environ.get('ANX_ROBOT_TLS','true').lower() in ('1','true','yes','on'); url=('https' if tls else 'http')+'://127.0.0.1:5000/health'; ctx=ssl._create_unverified_context() if tls else None; urllib.request.urlopen(url, context=ctx, timeout=5)"

CMD ["python", "/overlay/anx_web_entry.py"]
