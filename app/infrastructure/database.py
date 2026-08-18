from mongoengine import connect
import logging
import threading
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure, PyMongoError
import time
from config import Config

logger = logging.getLogger(__name__)

# Initialize MongoDB connection at module level
try:
    mongodb_uri = Config.MONGODB_URI
    if not mongodb_uri:
        logger.error("MONGODB_URI not set in configuration")
        raise ValueError("MONGODB_URI not set in configuration")
    

    # Connect to MongoDB using MongoEngine with resilient settings
    db = connect(
        host=mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=20000,  # tolera picos de lentidao do servidor (ex: build de indice em andamento) sem 500 em rotas nao relacionadas
        maxPoolSize=1,
        retryWrites=True,
        retryReads=True,
        alias='default'
    )
    logger.info("Successfully connected to MongoDB at module level")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB at module level: {str(e)}")
    raise

def _ensure_geo_indexes():
    """Create the 2dsphere indexes used by reverse_geocode(), with a generous timeout.

    Uses its own short-lived client instead of the shared maxPoolSize=1 connection so a
    slow first-time build on large collections doesn't starve the rest of the app.
    """
    client = None
    try:
        client = MongoClient(
            Config.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=600000,  # 10 min ceiling: index builds on large collections can take a while, but must not hang forever
        )
        db = client.get_database()
        db['streets'].create_index([('geometry', '2dsphere')])
        db['addresses'].create_index([('location', '2dsphere')])
        db['boundaries'].create_index([('geometry', '2dsphere')])
        logger.info("Geo indexes (streets/addresses/boundaries) verified/created")
    except PyMongoError as e:
        logger.warning(f"Could not verify/create geo indexes (reverse geocoding may be degraded until this succeeds): {str(e)}")
    finally:
        if client:
            client.close()


def _ensure_geo_indexes_async():
    """Kick off _ensure_geo_indexes() in a background daemon thread.

    Index creation must never block app startup/request handling — a slow or hung
    build here shouldn't take the whole API down with it.
    """
    threading.Thread(target=_ensure_geo_indexes, name='ensure-geo-indexes', daemon=True).start()


def init_app(app):
    """Initialize MongoDB connection with retry logic"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            # Test connection with proper error handling
            from mongoengine.connection import get_db
            db = get_db()
            
            # Simple ping command with timeout
            db.command('ping', maxTimeMS=5000)
            logger.info("Successfully verified MongoDB connection")
            
            # Initialize collections and indexes
            from app.presentation.auth_routes import TokenBlacklist
            TokenBlacklist.ensure_indexes()
            logger.info("Successfully initialized collections")

            # Geo indexes (2dsphere) for Street/Address/Boundary: built in the background with
            # a long-timeout client, since a first-time build on large imported OSM collections
            # can easily exceed the app's 5s socketTimeoutMS and must not block app startup.
            # MongoDB index builds (4.2+) run server-side, so reverse geocoding just stays
            # degraded (falls through to $geoNear errors) until the build finishes.
            _ensure_geo_indexes_async()
            
            return True
            
        except (ServerSelectionTimeoutError, ConnectionFailure) as e:
            if attempt < max_retries - 1:
                logger.warning(f"MongoDB connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(retry_delay)
                continue
            logger.error(f"Failed to connect to MongoDB after {max_retries} attempts: {str(e)}")
            raise
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise
