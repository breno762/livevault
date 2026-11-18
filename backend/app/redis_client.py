import redis

from .config import settings

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=15,
                                    socket_connect_timeout=5, health_check_interval=30)
