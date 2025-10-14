# DocSmart - Document Management REST API

A robust document management system built with Flask and MongoDB, implementing Clean Architecture principles.

## ✨ Features

- **User Authentication and Authorization** - JWT-based secure authentication
- **Vehicle Tracking** - GPS monitoring and vehicle management
- **Customer Management** - Complete customer lifecycle management
- **Role-based Access Control** - Granular permissions system
- **RESTful API** - Auto-generated Swagger/OpenAPI documentation
- **Firebase Storage** - Secure document and file storage

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Required Environment Variables

```bash
# Generate a secure secret key
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')

# Set MongoDB connection
export MONGODB_URI=mongodb://localhost:27017/docsmart
```

### 3. Create First Admin User

```bash
# Interactive mode
python bootstrap.py

# Or with command line arguments
python bootstrap.py --name "Admin User" --email admin@example.com --password "SecurePassword123"
```

### 4. Run the Application

**Development:**
```bash
python main.py
```

**Production:**
```bash
gunicorn -c gunicorn_config.py wsgi:app
```

The API will be available at `http://localhost:8000` with Swagger documentation at `http://localhost:8000/`

## 🏗️ Technical Stack

- **Framework**: Flask 3.0 + Flask-RESTX
- **Database**: MongoDB with MongoEngine ODM
- **Storage**: Firebase Cloud Storage
- **Authentication**: JWT (PyJWT 2.8)
- **Server**: Gunicorn (production)
- **Rate Limiting**: Flask-Limiter (Redis support)
- **CORS**: Flask-CORS

## 📡 API Endpoints

- `/api/auth` - Authentication (login, logout, token refresh, password recovery)
- `/api/users` - User management (CRUD with permission checks)
- `/api/permissions` - Permission management (admin only)
- `/api/vehicles` - Vehicle tracking and management
- `/api/customers` - Customer management
- `/api/links` - Link token validation (for email actions)

## 🔐 Security Features

- **JWT Authentication** with access & refresh tokens
- **Role-based Access Control** (RBAC) with granular permissions
- **Rate Limiting** to prevent abuse (configurable with Redis)
- **Password Hashing** using Werkzeug
- **Token Blacklisting** for revoked tokens
- **CORS Configuration** for cross-origin requests
- **Input Validation** with regex patterns
- **Secure File Uploads** with size limits

## ⚙️ Configuration

### Required Environment Variables

```bash
FLASK_SECRET_KEY=<your-secret-key>     # REQUIRED - Generate with: python -c 'import secrets; print(secrets.token_hex(32))'
MONGODB_URI=<mongodb-uri>              # REQUIRED
```

### Optional Environment Variables

```bash
# Server
PORT=8000

# CORS (default: *)
CORS_ORIGINS=https://yourapp.com,https://admin.yourapp.com

# Rate Limiting (recommended for production)
RATELIMIT_STORAGE_URL=redis://localhost:6379  # For production with Redis
RATELIMIT_STORAGE_URL=memory://               # For development (default)

# Firebase (for file storage)
FIREBASE_BUCKET_NAME=<your-bucket>

# Email (for password recovery)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=<your-email>
MAIL_PASSWORD=<your-app-password>
MAIL_DEFAULT_SENDER=noreply@yourapp.com

# Application URLs
APP_URL=https://yourapp.com
APP_URL_RECOVERY=https://yourapp.com/reset-password
APP_URL_DOCUMENT_SIGNATURE=https://yourapp.com/sign-document
```

## 🏛️ Architecture

The project follows **Clean Architecture** principles with clear separation of concerns:

```
├── app/
│   ├── domain/          # Core business logic and entities
│   │   └── models.py    # Domain models (User, Permission, Vehicle, Customer)
│   ├── application/     # Application services
│   │   ├── auth_service.py
│   │   └── link_token_service.py
│   ├── infrastructure/  # External services and database
│   │   ├── database.py
│   │   └── email_service.py
│   └── presentation/    # API routes and controllers
│       ├── auth_routes.py
│       ├── user_routes.py
│       └── ...
├── config.py           # Configuration management
├── main.py            # Application factory
├── wsgi.py            # WSGI entry point
├── gunicorn_config.py # Production server config
└── bootstrap.py       # Admin user creation script
```

### Key Design Patterns

- **Dependency Injection** - Services injected into routes
- **Repository Pattern** - MongoEngine as data access layer
- **Decorator Pattern** - `@token_required`, `@require_permission` for auth
- **Factory Pattern** - `create_app()` for application creation

## 🚢 Production Deployment

### Using Gunicorn (Recommended)

```bash
# With default configuration
gunicorn -c gunicorn_config.py wsgi:app

# With custom workers
GUNICORN_WORKERS=4 gunicorn -c gunicorn_config.py wsgi:app
```

### Using Docker

```bash
# Build image
docker build -t docsmart-api .

# Run container
docker run -p 8000:8000 \
  -e FLASK_SECRET_KEY=your-secret \
  -e MONGODB_URI=mongodb://host:27017/docsmart \
  docsmart-api
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions.

## 📊 Rate Limiting Configuration

### Development (In-Memory)

Default for development - limits reset on restart:

```bash
# No configuration needed, or explicitly:
export RATELIMIT_STORAGE_URL=memory://
```

### Production (Redis)

For production, use Redis for persistent, distributed rate limiting:

```bash
# Install Redis
sudo apt-get install redis-server

# Configure
export RATELIMIT_STORAGE_URL=redis://localhost:6379
```

## 🔧 Bootstrap Scripts

### Create Admin User

```bash
# Interactive mode (recommended for first time)
python bootstrap.py

# Command line mode
python bootstrap.py \
  --name "Admin User" \
  --email admin@example.com \
  --password "SecurePassword123"

# Only create/update permissions
python bootstrap.py --permissions-only
```

## 📚 Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide
- **[PRODUCTION_READINESS_REPORT.md](PRODUCTION_READINESS_REPORT.md)** - Production readiness analysis
- **Swagger UI** - Available at `/` when application is running

## ✅ Production Checklist

Before deploying to production:

- [ ] Set `FLASK_SECRET_KEY` to a secure random value
- [ ] Configure `MONGODB_URI` with production database
- [ ] Set up Redis for rate limiting (`RATELIMIT_STORAGE_URL`)
- [ ] Configure CORS with specific origins (not `*`)
- [ ] Enable HTTPS/SSL
- [ ] Create admin user with strong password
- [ ] Configure Firebase for file storage (if needed)
- [ ] Set up email service for password recovery (if needed)
- [ ] Configure logging and monitoring
- [ ] Set up automated backups

## 🐛 Troubleshooting

### Common Issues

**Error: "FLASK_SECRET_KEY must be set"**
```bash
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
```

**Error: "Cannot create first admin user"**
```bash
python bootstrap.py
```

**Warning: "Rate limiting using in-memory storage"**
```bash
export RATELIMIT_STORAGE_URL=redis://localhost:6379
```

## 🧪 Testing

The API can be tested using:

- **Swagger UI** - Interactive API documentation at `/`
- **cURL** - Command line HTTP requests
- **Postman** - Import OpenAPI spec from `/swagger.json`
- **Python requests** - Programmatic API testing

Example cURL request:
```bash
# Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"identifier": "admin@example.com", "password": "yourpassword"}'
```

## 📝 License

MIT License

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📞 Support

For issues, questions, or contributions:

- **Issues**: Open an issue on GitHub
- **Documentation**: See [DEPLOYMENT.md](DEPLOYMENT.md)
- **Security**: Report security issues privately
