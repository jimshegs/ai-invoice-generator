# Dockerfile
FROM python:3.11-slim

# Faster, cleaner Python in containers
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Fonts help PDF rendering (UTF-8/emoji). Keep lean.
RUN apt-get update && apt-get install -y \
    fonts-noto-color-emoji fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps; ensure playwright + gunicorn are present
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip install playwright gunicorn \
 && python -m playwright install --with-deps chromium

# Copy the rest of your code
COPY . .

# Expose Gunicorn port
EXPOSE 8000

# Start the app
CMD ["gunicorn", "app:app", "-w", "2", "-k", "gthread", "-b", "0.0.0.0:8000", "--timeout", "120"]
