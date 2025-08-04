from mongoengine import connect, Document, StringField, DateTimeField, ReferenceField, IntField
import logging
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
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
        socketTimeoutMS=5000,
        maxPoolSize=1,
        retryWrites=True,
        retryReads=True,
        alias='default'
    )
    logger.info("Successfully connected to MongoDB at module level")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB at module level: {str(e)}")
    raise

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
