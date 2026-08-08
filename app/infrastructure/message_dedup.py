import logging
import threading
import time
from config import Config

logger = logging.getLogger(__name__)

# WhatsApp Cloud API retries webhook delivery (same message id) when it
# doesn't get a fast 200 response — a slow downstream call (e.g. geocoding)
# is enough to trigger 2-3 retries within a few minutes. Dedup by message id
# so retries don't get reprocessed and re-sent to the user.
DEDUP_TTL_SECONDS = 600


class RedisMessageDeduplicator:

    def __init__(self, redis_url: str):
        import redis as redis_lib
        self._redis = redis_lib.from_url(redis_url, decode_responses=True)
        self._prefix = "chatbot:msgid:"
        self._redis.ping()
        logger.info("Redis message deduplicator initialized successfully")

    def seen_before(self, message_id: str) -> bool:
        try:
            key = f"{self._prefix}{message_id}"
            # SET NX: only succeeds if the key didn't exist yet.
            is_new = self._redis.set(key, "1", nx=True, ex=DEDUP_TTL_SECONDS)
            return not is_new
        except Exception as e:
            logger.error(f"Redis error in message dedup: {e}")
            return False


class InMemoryMessageDeduplicator:

    def __init__(self):
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        logger.info("In-memory message deduplicator initialized (dedup will not persist across restarts)")

    def seen_before(self, message_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune(now)

            if message_id in self._seen:
                return True

            self._seen[message_id] = now
            return False

    def _prune(self, now: float) -> None:
        expired = [mid for mid, ts in self._seen.items() if now - ts > DEDUP_TTL_SECONDS]
        for mid in expired:
            del self._seen[mid]


def _create_message_deduplicator():
    redis_url = Config.REDIS_URL
    if redis_url:
        try:
            return RedisMessageDeduplicator(redis_url)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.warning("Falling back to in-memory message deduplicator")
    return InMemoryMessageDeduplicator()


message_deduplicator = _create_message_deduplicator()
