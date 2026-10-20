# Photo Tools app on top of the headless Adobe DNG Converter image.
# Build the base first (see docker-compose.yml header):
#   docker build -t adobe-dng-converter:16.5 https://github.com/skykery/headless-adobe-dng-docker-image.git
ARG CONVERTER_IMAGE=adobe-dng-converter:16.5
FROM ${CONVERTER_IMAGE}

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

# Wine prefix lives in /root/.wine, so the app runs as root.
ENV DNG_CONVERTER=/usr/local/bin/convert-dng \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=5000 \
    FLASK_DEBUG=0 \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 5000

# Replace the converter image's convert-dng entrypoint with the web app
ENTRYPOINT ["/opt/venv/bin/python", "app.py"]
