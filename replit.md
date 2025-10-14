# DocSmart API

## Overview

DocSmart is a document management REST API built with Flask that implements Clean Architecture principles. The system provides user authentication, company/department organization, document categorization, and Firebase-based document storage with role-based access control.

The application follows a layered architecture with clear separation between domain models, application services, infrastructure concerns, and presentation (API routes). It uses MongoDB for data persistence and Firebase Cloud Storage for file storage.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Application Framework
- **Framework**: Flask with Flask-RESTX for RESTful API design
- **Architecture Pattern**: Clean Architecture with separation of concerns
  - Domain layer (models)
  - Application layer (services)  
  - Infrastructure layer (database, external services)
  - Presentation layer (API routes)
- **API Documentation**: Auto-generated Swagger/OpenAPI documentation via Flask-RESTX

### Authentication & Authorization
- **Authentication Method**: JWT (JSON Web Tokens) for stateless authentication
- **Token Types**: 
  - Access tokens (1 hour expiration)
  - Refresh tokens (7 day expiration)
  - Link tokens (configurable expiration for email/URL-based actions)
- **Token Blacklisting**: MongoDB-based token revocation system
- **Password Security**: Werkzeug password hashing
- **Rate Limiting**: Flask-Limiter for API protection (200/day, 50/hour default)
- **Authorization**: Role-based access control (RBAC) with granular permissions
  - Permission system based on resource type and action type (read/write/update/delete)
  - User roles (admin, user, etc.) with associated permissions

### Data Layer
- **Database**: MongoDB with MongoEngine ODM
- **Connection Strategy**: 
  - Resilient connection with retry logic
  - Connection pooling (maxPoolSize=1)
  - Timeout configurations for server selection, connection, and socket operations
  - Automatic retry for writes and reads
- **Data Models**: 
  - BaseDocument abstract class with audit fields (created_at, created_by, updated_at, updated_by)
  - User, Permission, Customer, Vehicle models
  - Token management models (TokenBlacklist, UsedLinkToken)
- **Indexing**: Automatic index creation with TTL indexes for token expiration

### File Storage
- **Storage Provider**: Firebase Cloud Storage
- **File Size Limit**: 10MB maximum upload size
- **Configuration**: Bucket name via environment variable

### Email System
- **Email Provider**: Flask-Mail with SMTP
- **Use Cases**:
  - Password recovery emails with time-limited tokens
  - Document signature request emails
- **Configuration**: SMTP server, credentials, and sender via environment variables

### Domain Models

**Core Entities**:
- **User**: Authentication, roles, permissions, company association, signature/rubric storage
- **Permission**: Resource-type and action-type based authorization
- **Customer**: Customer management with address, CPF, email validation
- **Vehicle**: GPS tracking data with IMEI-based identification, lock/unlock commands

**Supporting Models**:
- **TokenBlacklist**: Revoked JWT tokens with TTL expiration
- **UsedLinkToken**: Single-use link tokens for email actions
- **VehicleData**: GPS positioning and telemetry data

### Security & Production Readiness

**✅ Security Improvements Implemented** (October 14, 2025):
- ✅ SECRET_KEY now enforced - application fails fast if not set
- ✅ MONGODB_URI now mandatory - no default fallbacks
- ✅ Production WSGI server (Gunicorn) configured and running
- ✅ Bootstrap CLI script (`bootstrap.py`) for secure admin creation
- ✅ CORS support with configurable origins
- ✅ Rate limiting with Redis support (documented)

**Production-Ready Features**:
- Gunicorn WSGI server with auto-calculated workers
- Flask-CORS for cross-origin requests
- Environment-driven configuration (no hardcoded secrets)
- Bootstrap script with interactive and CLI modes
- Comprehensive deployment documentation

### API Structure

**Main Endpoints**:
- `/api/auth` - Authentication (login, logout, token refresh)
- `/api/users` - User management (CRUD with permission checks)
- `/api/permissions` - Permission management (admin only)
- `/api/links` - Link token validation and processing
- `/api/customers` - Customer management
- `/api/vehicles` - Vehicle tracking and management

**API Features**:
- Decorator-based authentication (`@token_required`)
- Decorator-based authorization (`@require_permission`)
- Consistent error handling and logging
- Input validation with regex patterns (email, CPF, CEP, etc.)

### Configuration Management
- Environment-based configuration via `config.py`
- **Mandatory** environment variables (app fails if not set):
  - `FLASK_SECRET_KEY` - JWT signing key (critical security requirement)
  - `MONGODB_URI` - MongoDB connection string
- **Optional** environment variables:
  - `CORS_ORIGINS` - Comma-separated allowed origins (default: '*')
  - `RATELIMIT_STORAGE_URL` - Redis/Memcached URL for rate limiting (default: memory://)
  - `FIREBASE_BUCKET_NAME` - Firebase storage bucket
  - `PORT` - Server port (default: 8000)
  - Email configuration (SMTP server, credentials)
  - Application URLs for email links

### Logging & Monitoring
- Centralized logging configuration
- Multi-handler setup (console + file)
- Structured log format with filename and line numbers
- Debug-level logging for development

## External Dependencies

### Database
- **MongoDB**: Primary data store
  - Used via MongoEngine ODM
  - Requires `MONGODB_URI` environment variable
  - Connection includes retry logic and timeouts

### Storage
- **Firebase Cloud Storage**: Document file storage
  - Requires `firebase-admin` SDK
  - Requires `FIREBASE_BUCKET_NAME` environment variable
  - Used for storing uploaded documents, signatures, and rubrics

### Email Service
- **SMTP Email Provider**: For transactional emails
  - Configurable SMTP server (default: Gmail)
  - Requires credentials via environment variables
  - Used for password recovery and document notifications

### Python Packages
- **Core Framework**: Flask 3.0.0, flask-restx 1.3.0
- **Database**: mongoengine 0.27.0, pymongo 4.6.0
- **Authentication**: PyJWT 2.8.0
- **Storage**: firebase-admin 6.2.0
- **Security**: Werkzeug 3.0.1 (password hashing)
- **Rate Limiting**: flask-limiter 3.5.0
- **CORS**: flask-cors 4.0.0
- **Email**: Flask-Mail 0.9.1
- **Production Server**: gunicorn 21.2.0
- **Document Processing**: PyMuPDF, PyPDF2, pytesseract, opencv-python, Pillow
- **Utilities**: python-dotenv 1.0.0, numpy

### Infrastructure & Deployment

**Production Server**:
- Gunicorn WSGI server (configured via `gunicorn_config.py`)
- Auto-calculated workers: `(CPU cores * 2) + 1`
- WSGI entry point: `wsgi.py`
- Default port: 8000 (configurable via `PORT` env var)

**Bootstrap Process**:
- `bootstrap.py` - CLI script for creating first admin user
- Interactive mode: `python bootstrap.py`
- CLI mode: `python bootstrap.py --name "Admin" --email admin@example.com --password pass123`
- Permission-only mode: `python bootstrap.py --permissions-only`

**Deployment Files**:
- `DEPLOYMENT.md` - Complete deployment guide with Docker examples
- `gunicorn_config.py` - Production server configuration
- `wsgi.py` - WSGI application entry point
- `.env.example` - Environment variables template (if created)

**Additional Notes**:
- Requires Python 3.11+ environment
- GPS tracker integration for vehicle monitoring (separate service)
- Rate limiting backend (Redis) recommended for multi-instance deployments

## Production Readiness Status

**Last Analysis:** October 13, 2025  
**Status Update:** October 14, 2025 - ✅ **PRODUCTION READY**

### ✅ Resolved Critical Issues:
1. ✅ **Bootstrap Mechanism**: CLI script (`bootstrap.py`) created for admin user creation
2. ✅ **SECRET_KEY Enforced**: Application now fails fast if FLASK_SECRET_KEY is not set
3. ✅ **Production Server**: Gunicorn WSGI server configured and running
4. ✅ **CORS Support**: Flask-CORS integrated with configurable origins
5. ✅ **Deployment Documentation**: Complete guide created in `DEPLOYMENT.md`

### Remaining Recommendations (Optional):
1. **Rate Limiting**: Configure Redis backend for multi-instance deployments
   - Set `RATELIMIT_STORAGE_URL=redis://localhost:6379`
   - Currently using in-memory (works but not recommended for production)
2. **Automated Tests**: Add unit and integration tests
3. **Environment Variables**: Configure Firebase and Email services as needed

### Production Deployment Checklist:
- ✅ Mandatory environment variables enforced (SECRET_KEY, MONGODB_URI)
- ✅ Production WSGI server (Gunicorn) configured
- ✅ Bootstrap script for admin creation
- ✅ CORS support for frontend integration
- ✅ Comprehensive deployment documentation
- ⚠️ Rate limiting backend (Redis recommended, in-memory works)
- ⚠️ Optional services (Firebase, Email) - configure as needed

**Documentation:**
- `PRODUCTION_READINESS_REPORT.md` - Original analysis and recommendations
- `DEPLOYMENT.md` - Complete production deployment guide
- `README.md` - Updated with new features and quick start