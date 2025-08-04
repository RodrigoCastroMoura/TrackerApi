# DocSmart - Document Management REST API

A robust document management system built with Flask and MongoDB, implementing Clean Architecture principles.

## Features

- User Authentication and Authorization
- Company Management
- Department Organization
- Document Categories
- Document Upload and Storage using Firebase
- Role-based Access Control
- RESTful API with Swagger Documentation

## Technical Stack

- **Backend Framework**: Flask with Flask-RESTX
- **Database**: MongoDB with MongoEngine ORM
- **Storage**: Firebase Cloud Storage
- **Authentication**: JWT-based authentication
- **Documentation**: Swagger/OpenAPI

## API Endpoints

- `/api/auth`: Authentication endpoints
- `/api/companies`: Company management
- `/api/users`: User management
- `/api/departments`: Department management
- `/api/categories`: Category management
- `/api/documents`: Document management

## Getting Started

1. Set up environment variables:
   ```
   MONGODB_URI=your_mongodb_uri
   FIREBASE_BUCKET_NAME=your_firebase_bucket
   FLASK_SECRET_KEY=your_secret_key
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python main.py
   ```

## Security Features

- JWT-based authentication
- Role-based access control (Admin/User roles)
- Request rate limiting
- Secure file uploads
- Input validation and sanitization

## Architecture

The project follows Clean Architecture principles with the following layers:

- **Domain**: Core business logic and entities
- **Infrastructure**: Database and external service implementations
- **Presentation**: API routes and controllers

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## License

MIT License
