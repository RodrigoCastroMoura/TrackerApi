import jwt
import base64
import os
from datetime import datetime, timedelta
from flask import current_app
from app.domain.models import User
from mongoengine import StringField, DateTimeField, Document
import logging
import re
from typing import Optional, Tuple, Dict, Any
from functools import wraps
from flask import request
from werkzeug.exceptions import Unauthorized, HTTPException

# Configure logging
logger = logging.getLogger(__name__)

class TokenBlacklist(Document):
    token = StringField(required=True, unique=True)
    blacklisted_at = DateTimeField(default=datetime.utcnow)
    expires_at = DateTimeField(required=True)
    meta = {'collection': 'token_blacklist'}

class AuthService:
    @staticmethod
    def create_token(user: User, is_refresh: bool = False) -> Tuple[str, datetime]:
        """Create a new JWT token with enhanced security and error handling"""
        try:
            # Verify SECRET_KEY is set
            secret_key = current_app.config.get('SECRET_KEY')
            if not secret_key:
                logger.error("SECRET_KEY not found in application config")
                raise ValueError("Application secret key is not configured")

            if not hasattr(user, 'email') or not hasattr(user, 'id') or not hasattr(user, 'role'):
                logger.error("Invalid user object: missing required attributes")
                raise ValueError("Invalid user object")

            logger.debug(f"Creating {'refresh' if is_refresh else 'access'} token for user: {user.email}")

            # Generate token expiration
            expiration = datetime.utcnow() + (
                timedelta(days=7) if is_refresh else timedelta(hours=1)
            )

            # Generate unique token ID
            token_id = AuthService._generate_token_id()

            # Prepare token payload
            payload = {
                'user_id': str(user.id),
                'email': user.email,
                'role': user.role,
                'company_id': str(user.company_id.id),
                'exp': int(expiration.timestamp()),
                'iat': int(datetime.utcnow().timestamp()),
                'type': 'refresh' if is_refresh else 'access',
                'jti': token_id
            }

            # Encode token
            try:
                token = jwt.encode(
                    payload, 
                    secret_key, 
                    algorithm='HS256'
                )
                # Ensure token is str type
                if isinstance(token, bytes):
                    token = token.decode('utf-8')

                logger.info(f"Token created successfully for user: {user.email}")
                return token, expiration

            except Exception as e:
                logger.error(f"Failed to encode JWT token: {str(e)}")
                raise ValueError(f"Error encoding authentication token: {str(e)}")

        except ValueError as e:
            logger.error(f"Value error in token creation: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in token creation: {str(e)}")
            raise ValueError(f"Error creating authentication token: {str(e)}")

    @staticmethod
    def _generate_token_id():
        """Generate a unique token ID for JWT"""
        return base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8')

    @staticmethod
    def verify_token(token: str) -> Optional[User]:
        """Verify token with enhanced security checks"""
        try:
            # Validate token format
            is_valid, error = AuthService._validate_token_format(token)
            if not is_valid:
                logger.warning(f"Token verification failed: {error}")
                return None

            # Check if token is blacklisted
            try:
                blacklisted = TokenBlacklist.objects(token=token).first()
                if blacklisted:
                    logger.warning("Token verification failed: Token is blacklisted")
                    return None
            except Exception as e:
                logger.error(f"Error checking token blacklist: {str(e)}")
                return None

            try:
                # Decode and verify token
                payload = jwt.decode(
                    token, 
                    current_app.config['SECRET_KEY'], 
                    algorithms=['HS256']
                )

                # Validate token type
                if payload.get('type') != 'access':
                    logger.warning("Token verification failed: Invalid token type")
                    return None

                # Validate required claims
                required_claims = ['user_id', 'email', 'role', 'company_id', 'exp', 'iat', 'type', 'jti']
                if not all(claim in payload for claim in required_claims):
                    logger.warning("Token verification failed: Missing required claims")
                    return None

                # Get and validate user
                try:
                    user = User.objects(id=payload['user_id']).first()
                    if not user:
                        logger.warning(f"User not found for token payload: {payload['user_id']}")
                        return None

                    # Validate user data matches token payload
                    if (str(user.company_id.id) != payload['company_id'] or
                        user.email != payload['email'] or
                        user.role != payload['role']):
                        logger.warning("Token verification failed: User data mismatch")
                        return None

                    logger.debug(f"Token verification successful for user: {user.email}")
                    return user
                except Exception as e:
                    logger.error(f"Error retrieving or validating user: {str(e)}")
                    return None

            except jwt.ExpiredSignatureError:
                logger.warning("Token verification failed: Token has expired")
                return None
            except jwt.InvalidTokenError as e:
                logger.warning(f"Token verification failed: Invalid token - {str(e)}")
                return None

        except Exception as e:
            logger.error(f"Token verification error: {str(e)}")
            return None

    @staticmethod
    def authenticate_user(identifier: str, password: str) -> Tuple[Optional[User], Dict[str, Any]]:
        """Authenticate user with enhanced security"""
        try:
            logger.info(f"Attempting to authenticate user with identifier: {identifier}")

            # Input validation
            if not identifier or not password:
                logger.warning("Authentication failed: Missing credentials")
                return None, {"error": "Both identifier and password are required"}

            # Validate identifier format
            if '@' in identifier:  # Email validation
                if not re.match(r"[^@]+@[^@]+\.[^@]+", identifier):
                    logger.warning(f"Authentication failed: Invalid email format - {identifier}")
                    return None, {"error": "Invalid email format"}
            else:  # CPF validation
                if not re.match(r"^\d{11}$", identifier):
                    logger.warning(f"Authentication failed: Invalid CPF format - {identifier}")
                    return None, {"error": "Invalid CPF format"}

            # Try to find user
            try:
                user = None
                if '@' in identifier:
                    user = User.objects(email=identifier).first()
                else:
                    user = User.objects(cpf=identifier).first()

                if not user:
                    logger.warning(f"Authentication failed: User not found with identifier - {identifier}")
                    return None, {"error": "Invalid credentials"}

                if not user.check_password(password):
                    logger.warning(f"Authentication failed: Invalid password for user - {identifier}")
                    return None, {"error": "Invalid credentials"}

                logger.info(f"Authentication successful for user: {user.email}")
                return user, {}
            except Exception as e:
                logger.error(f"Error finding/validating user: {str(e)}")
                return None, {"error": "Authentication error occurred"}

        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return None, {"error": f"Authentication error occurred: {str(e)}"}

    @staticmethod
    def blacklist_token(token: str) -> bool:
        """Add token to blacklist with enhanced validation"""
        try:
            # Validate token format
            is_valid, error = AuthService._validate_token_format(token)
            if not is_valid:
                logger.warning(f"Token blacklisting failed: {error}")
                return False

            try:
                payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
                expires_at = datetime.fromtimestamp(payload['exp'])

                # Check if token is already blacklisted
                blacklisted = TokenBlacklist.objects(token=token).first()
                if blacklisted:
                    logger.warning("Token already blacklisted")
                    return True

                blacklist_entry = TokenBlacklist(
                    token=token,
                    expires_at=expires_at
                )
                blacklist_entry.save()

                logger.info(f"Token blacklisted successfully, expires at: {expires_at}")
                return True

            except jwt.InvalidTokenError:
                logger.warning("Cannot blacklist invalid token")
                return False

        except Exception as e:
            logger.error(f"Error blacklisting token: {str(e)}")
            return False

    @staticmethod
    def _validate_token_format(token: str) -> Tuple[bool, Optional[str]]:
        """Validate JWT token format"""
        try:
            if not isinstance(token, str):
                return False, "Token must be a string"

            if not token:
                return False, "Token cannot be empty"

            parts = token.split('.')
            if len(parts) != 3:
                return False, "Invalid token format"

            for i, part in enumerate(['header', 'payload', 'signature']):
                try:
                    missing_padding = len(parts[i]) % 4
                    if missing_padding:
                        parts[i] += '=' * (4 - missing_padding)
                    base64.urlsafe_b64decode(parts[i])
                except Exception as e:
                    return False, f"Invalid {part} encoding"

            return True, None

        except Exception as e:
            logger.error(f"Token format validation error: {str(e)}")
            return False, "Invalid token format"

def token_required(f):
    """Token validation decorator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')

        if not auth_header:
            logger.warning("Authentication failed: No Authorization header")
            raise Unauthorized("Authorization header is required")

        try:
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                logger.warning("Authentication failed: Invalid Authorization header format")
                raise Unauthorized("Invalid Authorization header format. Expected: 'Bearer <token>'")

            token = parts[1]
            current_user = AuthService.verify_token(token)

            if not current_user:
                logger.warning("Authentication failed: Invalid or expired token")
                raise Unauthorized("Invalid or expired token")

            if not hasattr(current_user, 'email'):
                logger.error("Invalid user object returned from token verification")
                raise Unauthorized("Authentication failed")

            logger.debug(f"Token verification successful for user: {current_user.email}")
            return f(*args, current_user=current_user, **kwargs)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Token verification error: {str(e)}")
            raise Unauthorized("Authentication failed")

    return decorated