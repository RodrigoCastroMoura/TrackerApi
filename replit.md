# Sistema de Rastreamento Veicular - API

## Overview
This project is a comprehensive multi-tenant vehicle tracking system built with Flask, adhering to Clean Architecture principles. It offers user authentication, multi-company management (multi-tenancy), real-time GPS tracking, and detailed reports with role-based access control. The system utilizes MongoDB for data persistence and Firebase Cloud Storage for file storage. Its core purpose is to provide a robust and scalable solution for vehicle monitoring and management for various businesses, enabling efficient operations and data-driven insights.

## Recent Changes
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

### Python Packages (Key Examples)
- **Framework**: Flask, Flask-RESTX.
- **Database**: MongoEngine, PyMongo.
- **Authentication**: PyJWT, Werkzeug.
- **Storage**: Firebase-admin.
- **Geocoding**: Geopy (Nominatim), Googlemaps (Google Maps API).
- **Payment**: Mercadopago.
- **Production Server**: Gunicorn.