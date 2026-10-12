FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV TRAILKIT_DB=/data/trailkit.db
VOLUME ["/data"]
EXPOSE 8088
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8088"]
