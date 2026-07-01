# Dockerfile
#
# Builds a container with Python + all dependencies installed, ready to
# run any of the jobs/bot scripts. Does NOT schedule anything by itself -
# scheduling is handled by whatever runs this container (Railway's cron
# jobs, a VPS's crontab, or GitHub Actions). See README for setup.

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (separate layer, only rebuilds if
# requirements.txt changes - speeds up rebuilds when you edit job code)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# No CMD here on purpose - which script to run is decided at run time
# (see docker-compose.yml or Railway's per-service start command), since
# this one image needs to run several different jobs on different
# schedules, not a single fixed process.
