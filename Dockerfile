FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    make \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

RUN make gen

# RUN apt-get remove -y make && apt-get autoremove -y

EXPOSE 50051
CMD ["python", "server.py"]