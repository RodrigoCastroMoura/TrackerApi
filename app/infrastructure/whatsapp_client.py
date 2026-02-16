import requests
import logging
from config import Config

logger = logging.getLogger(__name__)


class WhatsAppClient:

    def __init__(self):
        self.api_url = Config.WHATSAPP_API_URL
        self.phone_number_id = Config.WHATSAPP_PHONE_NUMBER_ID
        self.token = Config.WHATSAPP_TOKEN

    @property
    def base_url(self):
        return f"{self.api_url}/{self.phone_number_id}/messages"

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def send_message(self, to: str, text: str) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }
        return self._send(payload)

    def send_interactive_buttons(self, to: str, body_text: str, buttons: list) -> bool:
        formatted_buttons = []
        for btn in buttons[:3]:
            formatted_buttons.append({
                "type": "reply",
                "reply": {
                    "id": btn["id"],
                    "title": btn["title"][:20]
                }
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {
                    "buttons": formatted_buttons
                }
            }
        }
        return self._send(payload)

    def send_list(self, to: str, body_text: str, button_text: str, sections: list) -> bool:
        formatted_sections = []
        for section in sections:
            rows = []
            for row in section.get("rows", [])[:10]:
                rows.append({
                    "id": str(row["id"])[:200],
                    "title": str(row["title"])[:24],
                    "description": str(row.get("description", ""))[:72]
                })
            formatted_sections.append({
                "title": section.get("title", "")[:24],
                "rows": rows
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text[:1024]},
                "action": {
                    "button": button_text[:20],
                    "sections": formatted_sections
                }
            }
        }
        return self._send(payload)

    def _send(self, payload: dict) -> bool:
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"Message sent to {payload.get('to', 'unknown')}")
                return True
            else:
                logger.error(f"WhatsApp API error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {str(e)}")
            return False


whatsapp_client = WhatsAppClient()
