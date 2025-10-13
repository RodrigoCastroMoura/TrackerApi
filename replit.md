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

### Security Considerations

**Current Security Issues** (as noted in production readiness report):
- Default SECRET_KEY fallback poses critical vulnerability
- Development server not suitable for production
- No bootstrap mechanism for initial admin user creation

**Required Security Improvements**:
- Enforce SECRET_KEY via environment variable (fail if not set)
- Deploy with production WSGI server (Gunicorn included in requirements)
- Implement secure admin bootstrap process (CLI or one-time setup endpoint)

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
- Required environment variables:
  - `MONGODB_URI` - MongoDB connection string
  - `FIREBASE_BUCKET_NAME` - Firebase storage bucket
  - `FLASK_SECRET_KEY` - JWT signing key (critical)
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
- **Email**: Flask-Mail 0.9.1
- **Production Server**: gunicorn 21.2.0
- **Document Processing**: PyMuPDF, PyPDF2, pytesseract, opencv-python, Pillow
- **Utilities**: python-dotenv 1.0.0, numpy

### Infrastructure Notes
- Application designed to run on port 8000 (configurable)
- Supports deployment with Gunicorn WSGI server
- Requires Python 3.x environment
- GPS tracker integration for vehicle monitoring (separate systemd service referenced in logs)

## Production Readiness Status

**Last Analysis:** October 13, 2025  
**Status:** ❌ NOT PRODUCTION READY

### Critical Issues Blocking Production:
1. **No Bootstrap Mechanism**: Cannot create first admin user without direct database access
2. **SECRET_KEY Vulnerability**: Falls back to default value 'default-secret-key' if not configured
3. **Development Server**: Using Flask development server instead of production WSGI server
4. **No Automated Tests**: No test coverage for critical functionality

### High Priority Issues:
1. **Rate Limiting**: Using in-memory storage, not suitable for production/multiple instances
2. **No CORS Configuration**: Will block frontend web access from different domains
3. **Missing Environment Variables**: Firebase and Email services not configured
4. **No Deployment Documentation**: Missing production deployment instructions

### Positive Findings:
- Clean Architecture implementation
- Good security practices (password hashing, token blacklist)
- Adequate input validation
- Structured error handling and logging
- Sensitive data protection (card_token not exposed in API responses)

**Full Report:** See `PRODUCTION_READINESS_REPORT.md` for detailed analysis and recommendations