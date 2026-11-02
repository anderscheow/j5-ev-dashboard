# The relay alone. Build context is this folder, never the repository root, so the
# dashboard next to it stays out of this image:
#
#   docker build -t voltmoss-push-relay push_relay/
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/push_relay/requirements.txt
RUN pip install --no-cache-dir -r /app/push_relay/requirements.txt
COPY . /app/push_relay/
ENV VOLTMOSS_RELAY_DB=/data/push_relay.db
ENV VOLTMOSS_RELAY_BIND=0.0.0.0
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "push_relay.server"]
