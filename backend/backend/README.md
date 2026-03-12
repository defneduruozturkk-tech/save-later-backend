# Save Later Backend

FastAPI backend for the Save Later mobile application.

## Features

- User authentication with Google OAuth
- Location management (CRUD operations)
- List management with sharing functionality
- Google Places API integration for location search
- URL metadata scraping
- MongoDB database

## Tech Stack

- **Framework**: FastAPI
- **Database**: MongoDB
- **Authentication**: JWT + Google OAuth
- **Deployment**: Railway

## Environment Variables

Required environment variables (set in Railway):

```
MONGO_URL=<your-mongodb-connection-string>
DB_NAME=savelater_production
JWT_SECRET_KEY=<your-secret-key>
GOOGLE_PLACES_API_KEY=<your-google-places-key>
GOOGLE_MAPS_API_KEY=<your-google-maps-key>
ENVIRONMENT=production
CORS_ORIGINS=*
```

## Deployment

This app is configured for Railway deployment. See `RAILWAY_DEPLOYMENT.md` for detailed instructions.

## API Endpoints

- `POST /api/auth/google` - Google OAuth login
- `GET /api/locations` - Get user's saved locations
- `POST /api/locations` - Create new location
- `GET /api/lists` - Get user's lists
- `POST /api/lists` - Create new list
- `POST /api/places/search` - Search places via Google Places API
- `POST /api/scrape` - Scrape metadata from URL

## Local Development

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8001
```
