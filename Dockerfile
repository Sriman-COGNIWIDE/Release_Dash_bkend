FROM python:3.12

WORKDIR /App

COPY /Backend/requirements.txt .
COPY /Backend/. .

RUN apt-get update && apt-get install -y \
    curl \
&& rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

ENTRYPOINT ["python3", "-u", "app.py"]
