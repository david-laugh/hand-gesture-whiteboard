FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8002

# Scaffold placeholder: no application yet.
# TODO: CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]
CMD ["sleep", "infinity"]
