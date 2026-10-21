FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY nibe_web.py nibe_app.py nibe_ui.js nibe_hmi.py nibe_hmi.js nibe_firmware.py nibe_http.py LICENSE /app/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "/app/nibe_app.py"]
LABEL io.hass.name="NIBE Local HMI" \
      io.hass.description="Local NIBE menu interface" \
      io.hass.type="addon" \
      io.hass.version="0.6.3" \
      org.opencontainers.image.source="https://github.com/Oniel10/nibe-local-hmi" \
      org.opencontainers.image.licenses="LicenseRef-NIBE-Local-HMI-NC-ND-1.0"
