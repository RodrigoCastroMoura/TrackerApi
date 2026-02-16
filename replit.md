# Sistema de Rastreamento Veicular - API

## Overview
This project is a comprehensive multi-tenant vehicle tracking system built with Flask, adhering to Clean Architecture principles. It offers user authentication, multi-company management (multi-tenancy), real-time GPS tracking, and detailed reports with role-based access control. The system utilizes MongoDB for data persistence and Firebase Cloud Storage for file storage. Its core purpose is to provide a robust and scalable solution for vehicle monitoring and management for various businesses, enabling efficient operations and data-driven insights.

## Recent Changes
- **Feb 16, 2026**: Added WhatsApp chatbot module (`app/chatbot/`) for customer interaction via WhatsApp. Supports auto-authentication by phone number, vehicle selection (interactive lists/buttons), location tracking, and block/unblock commands. Integrated as Flask Blueprint at `/api/chatbot/webhook`.
- **Nov 24, 2025 (Security Update)**: Enhanced tracking list endpoint with automatic customer_id filtering. Removed customer_id query parameter from GET /tracking/vehicles - now automatically extracted from JWT token for customer users. This prevents customers from accessing vehicles belonging to other customers in the vehicle list view.
- **Nov 24, 2025 (Email Configuration)**: Fixed MAIL_DEFAULT_SENDER configuration - now automatically uses MAIL_USERNAME when not explicitly set. Registered all email credentials as encrypted secrets for secure deployment.
- **Nov 24, 2025**: Redesigned password recovery system with temporary password flow. Removed token-based password reset endpoints (/password/reset, /customer/password/reset). New flow: Recovery generates random 12-char temporary password, sets must_change_password=True and password_changed=False flags, sends email with credentials. JWT tokens include must_change_password flag for frontend enforcement. Password change endpoints allow forced updates without current password validation.
- **Nov 22, 2025**: Added vehicle type classification field ("tipo") to Vehicle model with support across all CRUD and tracking endpoints. Vehicles can now be classified as: carro, moto, caminhao, van, onibus, outro.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### Application Framework
- **Framework**: Flask with Flask-RESTX for RESTful API design.
- **Architecture Pattern**: Clean Architecture with distinct layers: Domain, Application, Infrastructure, and Presentation.
- **API Documentation**: Auto-generated Swagger/OpenAPI via Flask-RESTX.

### Authentication & Authorization
- **Authentication**: JWT (JSON Web Tokens) with access, refresh, and link tokens.
- **Token Management**: MongoDB-based token blacklisting and Werkzeug for password hashing.
- **Rate Limiting**: Flask-Limiter for API protection.
- **Authorization**: Role-based access control (RBAC) with granular permissions based on resource and action types.

### Data Layer
- **Database**: MongoDB with MongoEngine ODM.
- **Connection Strategy**: Resilient connection with retry logic, connection pooling, and timeouts.
- **Data Models**: Includes `BaseDocument` for audit fields, `Company`, `User`, `Permission`, `Customer`, `Vehicle`, `VehicleData`, `TokenBlacklist`, `UsedLinkToken`. Also includes `Subscription` and `Payment` for Mercado Pago integration.
- **Multi-Tenancy**: Data isolation by `company_id` across all primary resources.
- **Indexing**: Automatic index creation, including TTL indexes for token expiration.

### File Storage
- **Provider**: Firebase Cloud Storage for documents, signatures, and rubrics.
- **Limit**: 10MB maximum upload size.

### Email System
- **Provider**: Flask-Mail with SMTP.
- **Use Cases**: Password recovery and document signature requests.

### Domain Models
- **Core Entities**: `Company`, `User`, `Permission`, `Customer`, `Vehicle`.
- **Supporting Models**: `TokenBlacklist`, `UsedLinkToken`, `VehicleData` (GPS data).
- **Payment Models**: `SubscriptionPlan` (planos de assinatura), `Subscription` (assinaturas ativas), `Payment` (histórico de pagamentos).

### Security & Production Readiness
- Mandatory environment variables: `FLASK_SECRET_KEY`, `MONGODB_URI`.
- **Webhook Security**: HMAC-SHA256 signature validation for Mercado Pago webhooks using `MERCADOPAGO_WEBHOOK_SECRET`.
- Production WSGI server: Gunicorn.
- CORS support with configurable origins.
- Bootstrap CLI script (`bootstrap.py`) for secure admin creation.
- **Security Updates (Nov 2025)**: Added webhook signature validation to prevent unauthorized webhook requests.

### API Structure
- **Main Endpoints**:
    - `/api/auth`: User and customer authentication (login, logout, refresh, password management).
    - `/api/users`: User/technician management.
    - `/api/permissions`: Permission management (admin only).
    - `/api/links`: Link token validation.
    - `/api/customers`: Customer management (multi-tenant).
    - `/api/vehicles`: Vehicle management (multi-tenant, includes lock/unlock commands).
    - `/api/tracking/vehicles`: GPS tracking endpoints (last known location, history, route) with geocoding.
    - `/api/reports`: Vehicle usage reports (summary, detailed, trips, stops).
    - `/api/subscription-plans`: Subscription plan management (list, create, update, delete).
    - `/api/subscriptions`: Monthly subscription management (create, view, cancel) and payment history (customer-only).
    - `/api/webhooks/mercadopago`: Mercado Pago payment notification processing.
    - `/api/chatbot/webhook`: WhatsApp chatbot webhook (GET=verification, POST=messages).
- **Features**: Decorator-based authentication and authorization, multi-tenancy enforcement, consistent error handling, input validation, pagination, and filtering.

### Configuration Management
- Environment-based configuration via `config.py`.
- Critical environment variables: `FLASK_SECRET_KEY`, `MONGODB_URI`.
- Optional variables for CORS, Rate Limiting, Firebase, Mercado Pago, Email, and Google Maps (`GOOGLE_MAPS_API_KEY`).

### Logging & Monitoring
- Centralized logging (console + file) with structured format.

## External Dependencies

### Database
- **MongoDB**: Primary database, accessed via MongoEngine.

### Storage
- **Firebase Cloud Storage**: For file storage, using `firebase-admin` SDK.

### Email Service
- **SMTP Email Provider**: Configurable for transactional emails via Flask-Mail.

### Geocoding Services
- **Nominatim (OpenStreetMap)**: Free reverse geocoding service via `geopy` library. Includes rate limiting (1 req/sec) and LRU caching.
- **Google Maps Geocoding API**: Premium reverse geocoding service via `googlemaps` library. Higher quality addresses, requires `GOOGLE_MAPS_API_KEY` environment variable. Includes LRU caching and automatic error handling.

### Payment Gateway
- **Mercado Pago**: For monthly subscription payment processing, integrated via `mercadopago` SDK. Supports payment links, subscription management, and webhooks.

### WhatsApp Chatbot
- **Provider**: WhatsApp Cloud API (Meta) via `app/chatbot/` module.
- **Webhook Endpoint**: `/api/chatbot/webhook` (GET for verification, POST for messages).
- **Features**: State-machine based conversation flow (UNAUTHENTICATED → AUTHENTICATED → VEHICLE_SELECTED). Supports vehicle location, block/unblock commands, interactive buttons and lists.
- **Session Management**: In-memory sessions with configurable timeout (`SESSION_TIMEOUT_MINUTES`), thread-safe.
- **Auth Flow**: Auto-authenticates by phone number using `PASSWORD_CHATBOT_SALT`, with CPF/password fallback.
- **Environment Variables**: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `PASSWORD_CHATBOT_SALT`, `WHATSAPP_API_URL`, `API_BASE_URL`, `SESSION_TIMEOUT_MINUTES`.
- **Module Structure**: `config.py` (settings), `models.py` (Session/Vehicle/ChatUser), `whatsapp_client.py` (API client), `session_manager.py` (session tracking), `business_service.py` (API calls), `message_handlers.py` (state machine), `routes.py` (webhook).

### Python Packages (Key Examples)
- **Framework**: Flask, Flask-RESTX.
- **Database**: MongoEngine, PyMongo.
- **Authentication**: PyJWT, Werkzeug.
- **Storage**: Firebase-admin.
- **Geocoding**: Geopy (Nominatim), Googlemaps (Google Maps API).
- **Payment**: Mercadopago.
- **Production Server**: Gunicorn.