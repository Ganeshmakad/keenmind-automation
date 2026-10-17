FROM python:3.12-slim

# Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY core/ core/
COPY api/ api/
COPY config/ config/
COPY cli.py .

# Create volume directories
RUN mkdir -p output cache temp

EXPOSE 9876

# Run FastAPI server
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "9876"]
