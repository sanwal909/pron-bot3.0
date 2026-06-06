from bot import bot
from flask import Flask, request
import threading
import os

import logging

# Flask app for Railway web service
app = Flask(__name__)

# Clean logs: Set Flask and Werkzeug to WARNING only
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)
app.logger.setLevel(logging.WARNING)

# Run bot in background thread
def run_bot():
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()

# Health check endpoint (required for Railway)
@app.route('/')
def home():
    return "Bot is running!", 200

@app.route('/health')
def health():
    return "OK", 200

# Optional: Webhook endpoint if you want to use webhooks instead of polling
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    bot.process_new_updates([telebot.types.Update.de_json(update)])
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
