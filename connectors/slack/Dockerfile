# BioMate Slack connector — self-host image.
# Build context is this directory (connectors/slack/).
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY slack_bot.py .

EXPOSE 8090

# Single worker keeps the in-memory per-user conversation history coherent
# (a user's follow-up landing on a different worker would lose context);
# threads give concurrency. Scaling to multiple workers/replicas requires
# moving _conversation_history to a shared store (Redis/DB) — see DEPLOY.md.
CMD ["gunicorn", "slack_bot:create_flask_app()", \
     "-b", "0.0.0.0:8090", "-w", "1", "--threads", "8", \
     "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-"]
