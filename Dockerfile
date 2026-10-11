FROM python:3.12-slim

# Prevent Python from writing .pyc files & buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg-binary ships its own libpq, so no compiler / system libs are needed.

# Install Python dependencies (gunicorn + whitenoise are in requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code (.dockerignore keeps venv/, .env, caches out)
COPY . .

# Django admin CSS/JS, served by WhiteNoise. Settings need a DATABASE_URL to
# import, so give collectstatic a placeholder - it never connects.
RUN DATABASE_URL=postgres://build:build@localhost:5432/build python manage.py collectstatic --noinput

# Run as an unprivileged user
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# Apply migrations on every start, then hand over to the CMD.
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--forwarded-allow-ips=*"]
