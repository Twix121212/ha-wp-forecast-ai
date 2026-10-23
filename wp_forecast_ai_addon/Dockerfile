ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}
WORKDIR /app
RUN apk add --no-cache mariadb-connector-c
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wp_forecast_ai /app/wp_forecast_ai
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh
CMD ["/app/run.sh"]
