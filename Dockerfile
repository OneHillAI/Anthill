FROM python:3.11-slim

WORKDIR /app

# Install build tools needed for some wheels (numpy, scipy)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY anthill/ ./anthill/

RUN pip install --no-cache-dir -e .

# Pre-download BGE-M3 so containers start fast (cached in the image layer).
# Remove this RUN line if you want a lean image and are willing to wait on first use.
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

ENTRYPOINT []
