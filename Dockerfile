FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tehran

# Install Octave, Gnuplot, Python and required tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    octave \
    octave-signal \
    octave-control \
    gnuplot \
    ghostscript \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Set up user named "user" with UID 1000 for Hugging Face Spaces compatibility
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

# Ensure storage directories are writable
RUN mkdir -p plots_storage && chmod 777 plots_storage

# Expose default port (Render overrides via $PORT)
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
