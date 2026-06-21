# BioMate Feishu/Lark connector — self-host image.
# Build context is this directory (connectors/feishu/).
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY feishu_bot.py .

EXPOSE 8093

# Single worker keeps the in-memory bind/history store coherent (binding a user
# on one worker but handling their next message on another would lose the bind);
# threads give concurrency. Scaling to multiple workers/replicas requires moving
# _user_bindings to a shared store (Redis/DB) — see DEPLOY.md.
CMD ["gunicorn", "feishu_bot:create_flask_app()", \
     "-b", "0.0.0.0:8093", "-w", "1", "--threads", "8", \
     "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-"]
