FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY beg_k_sebe_bot/ ./beg_k_sebe_bot/

CMD ["python", "-m", "beg_k_sebe_bot.bot.main"]
