FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py desktop_cdp.py desktop_players.py player_dom.js desktop_observer.js ./
EXPOSE 9980
CMD ["python", "/app/app.py"]
