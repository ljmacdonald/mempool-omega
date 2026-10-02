FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install -e ".[stream,dashboard]"
# never run as root
RUN useradd -m omega && chown -R omega /app
USER omega
CMD ["python", "-m", "paper.trader", "--once"]
