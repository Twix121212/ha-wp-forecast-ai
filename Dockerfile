ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh /app/run.sh
COPY wp_forecast_ai /app/wp_forecast_ai
RUN chmod a+x /app/run.sh

CMD ["/app/run.sh"]
