import logging
import hmac
import hashlib
from flask import Blueprint, request, jsonify
from app.chatbot.config import ChatbotConfig
from app.chatbot.session_manager import session_manager
from app.chatbot.message_handlers import message_handler

logger = logging.getLogger(__name__)

chatbot_bp = Blueprint('chatbot', __name__)


def verify_webhook_signature(request_obj) -> bool:
    if not ChatbotConfig.APP_SECRET:
        return True

    signature = request_obj.headers.get("X-Hub-Signature-256", "")
    if not signature:
        return False

    payload = request_obj.get_data()
    expected = "sha256=" + hmac.new(
        ChatbotConfig.APP_SECRET.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@chatbot_bp.route('/webhook', methods=['GET'])
def verify():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == ChatbotConfig.VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return challenge, 200

    logger.warning(f"Webhook verification failed: mode={mode}, token={token}")
    return 'Forbidden', 403


@chatbot_bp.route('/webhook', methods=['POST'])
def webhook():
    try:
        if not verify_webhook_signature(request):
            logger.warning("Invalid webhook signature")
            return jsonify({"status": "error", "message": "Invalid signature"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"status": "ok"}), 200

        entry = data.get("entry", [])
        for e in entry:
            changes = e.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])

                for msg in messages:
                    phone_number = msg.get("from", "")
                    msg_type = msg.get("type", "")

                    text = ""
                    message_type = "text"

                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {})
                        interactive_type = interactive.get("type", "")

                        if interactive_type == "button_reply":
                            text = interactive.get("button_reply", {}).get("id", "")
                            message_type = "interactive"
                        elif interactive_type == "list_reply":
                            text = interactive.get("list_reply", {}).get("id", "")
                            message_type = "interactive"
                    else:
                        continue

                    if text and phone_number:
                        logger.info(f"[WEBHOOK] From: {phone_number} | Type: {message_type} | Text: '{text}'")
                        session = session_manager.get_or_create(phone_number)
                        message_handler.handle(session, text, message_type)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return jsonify({"status": "error"}), 500
