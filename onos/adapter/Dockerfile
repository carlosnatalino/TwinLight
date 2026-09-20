# The adapter is pure Python with no GNPy dependency, so unlike the twin image
# it builds natively on both arm64 and amd64 -- no Rosetta/qemu penalty. Leave
# the platform unpinned so it stays fast on Apple Silicon; sharing a compose
# network with amd64 containers is fine.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tapi_adapter.py .

# Matches ADAPTER_PORT in docker-compose.onos.yml.
EXPOSE 8282

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8282/health').status==200 else 1)"

CMD ["uvicorn", "tapi_adapter:app", "--host", "0.0.0.0", "--port", "8282"]
