# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgl1/libglib2.0-0: OpenCV (pulled in by mediapipe) at import time.
# libegl1/libgles2: the mediapipe 1.x Tasks runtime dlopens them when creating
# HandLandmarker, even for CPU inference (verified: "libEGL.so.1: cannot open shared object file").
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libegl1 libgles2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Official MediaPipe Hand Landmarker model (float16, version 1).
# Stored outside /app so the dev bind mount does not hide it.
# The build fails if the download fails or the checksum does not match.
ADD --checksum=sha256:fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1 \
    https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task \
    /opt/models/hand_landmarker.task
RUN chmod 0444 /opt/models/hand_landmarker.task

COPY src ./src

ENV MODEL_PATH=/opt/models/hand_landmarker.task

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
