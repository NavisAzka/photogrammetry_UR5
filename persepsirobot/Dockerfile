FROM python:3.11-slim

# System tools: v4l2-ctl for camera config, curl/jq/unzip for run_odm.sh
# libgl1 + libglib2.0-0 required by OpenCV at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        v4l-utils \
        curl \
        jq \
        unzip \
        bash \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Script/ ./Script/

RUN mkdir -p /app/datasets /app/output

EXPOSE 12345

CMD ["python3", "-u", "Script/app.py"]
