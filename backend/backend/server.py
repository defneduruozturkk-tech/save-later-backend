from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
from jose import JWTError, jwt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import httpx
from bs4 import BeautifulSoup
import re


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Google API Keys (loaded from environment)
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# JWT Configuration
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# Create the main app without a prefix
app = FastAPI(title="Save Later API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# ============================================================================
# MODELS
# ============================================================================

class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    google_id: str
    email: str
    name: str
    profile_picture: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SavedLocation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    category: str  # Food, Coffee, Bar, Holiday, Activity, Shopping, Other
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ListModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    name: str
    description: Optional[str] = None
    location_ids: List[str] = []
    is_public: bool = False
    share_token: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class GoogleLoginRequest(BaseModel):
    id_token: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

class LocationCreate(BaseModel):
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    category: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LocationUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    category: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class ScrapeRequest(BaseModel):
    url: str

class ScrapeResponse(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location_suggestion: Optional[str] = None

class PlaceSearchRequest(BaseModel):
    query: str

class PlaceSearchResponse(BaseModel):
    places: List[dict]

class ListCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_public: bool = False

class ListUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None

class AddLocationToListRequest(BaseModel):
    location_id: str


# ============================================================================
# AUTHENTICATION HELPERS
# ============================================================================

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        parts = authorization.split()
        if len(parts) != 2:
            raise HTTPException(status_code=401, detail="Invalid authorization header format")
        scheme, token = parts
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await db.users.find_one({"id": user_id})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        return User(**{k: v for k, v in user.items() if k != '_id'})
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid authorization header: {str(e)}")


# ============================================================================
# SCRAPING HELPERS
# ============================================================================

def extract_location_from_text(text: str) -> Optional[str]:
    """Enhanced location extraction from text using multiple strategies"""
    if not text:
        return None
    
    # Strategy 1: Location pin emoji patterns (most reliable from social media)
    emoji_patterns = [
        r'📍\s*([^\n,]{3,50})',           # 📍 Location Name
        r'📌\s*([^\n,]{3,50})',           # 📌 Location Name  
        r'🏠\s*([^\n,]{3,50})',           # 🏠 Location Name
        r'🗺️?\s*([^\n,]{3,50})',          # 🗺 Location Name
    ]
    
    for pattern in emoji_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    
    # Strategy 2: Explicit location markers
    explicit_patterns = [
        r'[Ll]ocation[:\s]+([A-Z][a-zA-ZÀ-ÿ\s,\-\']+)',   # Location: Place Name
        r'[Aa]dres[si]?[:\s]+([A-Z][a-zA-ZÀ-ÿ\s,\-\']+)',  # Adres/Address: ...
        r'[Ww]here[:\s]+([A-Z][a-zA-ZÀ-ÿ\s,\-\']+)',       # Where: ...
    ]
    
    for pattern in explicit_patterns:
        match = re.search(pattern, text)
        if match:
            location = match.group(1).strip()
            location = re.sub(r'\s+', ' ', location)
            if len(location) > 3:
                return location[:80]
    
    # Strategy 3: "at/in" patterns with proper nouns
    context_patterns = [
        r'(?:^|\s)(?:at|in|@)\s+([A-Z][a-zA-ZÀ-ÿ\'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ\'\-]+){0,4}(?:,\s*[A-Z][a-zA-ZÀ-ÿ\'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ\'\-]+){0,2})?)',
    ]
    
    for pattern in context_patterns:
        match = re.search(pattern, text)
        if match:
            location = match.group(1).strip()
            location = re.sub(r'\s+', ' ', location)
            # Filter out common false positives
            false_positives = {'The', 'This', 'That', 'Here', 'There', 'Home', 'Work', 'My', 'Our', 'Your'}
            if location.split()[0] not in false_positives and len(location) > 3:
                return location[:80]
    
    # Strategy 4: Look for city/country names in text
    known_cities = [
        'Istanbul', 'İstanbul', 'Ankara', 'Izmir', 'İzmir', 'Antalya', 'Bodrum', 'Kapadokya', 'Cappadocia',
        'Paris', 'London', 'Rome', 'Roma', 'Barcelona', 'Amsterdam', 'Berlin', 'Vienna', 'Prague',
        'New York', 'Los Angeles', 'Tokyo', 'Dubai', 'Bangkok', 'Bali', 'Milan', 'Milano',
        'Lisbon', 'Lisboa', 'Athens', 'Santorini', 'Mykonos', 'Florence', 'Firenze',
    ]
    
    for city in known_cities:
        if city.lower() in text.lower():
            # Try to find more context around the city name
            city_pattern = rf'([A-ZÀ-ÿ][a-zA-ZÀ-ÿ\'\-]+(?:\s+[A-ZÀ-ÿ][a-zA-ZÀ-ÿ\'\-]+){{0,2}}[\s,]+)?{re.escape(city)}'
            match = re.search(city_pattern, text, re.IGNORECASE)
            if match and match.group(1):
                return f"{match.group(1).strip()} {city}".strip()
            return city
    
    return None

async def scrape_metadata(url: str) -> ScrapeResponse:
    """Scrape metadata from URL with platform-specific handling"""
    try:
        # Social media platforms serve full metadata to crawler bots
        social_domains = ['tiktok.com', 'instagram.com', 'twitter.com', 'x.com', 'facebook.com', 'threads.net']
        is_social = any(d in url for d in social_domains)
        
        user_agent = 'facebookexternalhit/1.1' if is_social else 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resolved_url = url
            
            # Resolve short URLs
            try:
                head_resp = await client.head(url, headers={'User-Agent': user_agent})
                resolved_url = str(head_resp.url)
            except Exception:
                pass
            
            is_social = is_social or any(d in resolved_url for d in social_domains)
            if is_social:
                user_agent = 'facebookexternalhit/1.1'
            
            # TikTok video posts: try oEmbed first
            if 'tiktok.com' in resolved_url and '/video/' in resolved_url:
                try:
                    oembed_url = f"https://www.tiktok.com/oembed?url={resolved_url}"
                    oembed_resp = await client.get(oembed_url)
                    if oembed_resp.status_code == 200:
                        data = oembed_resp.json()
                        title = data.get('title', '').strip() or data.get('author_name', '')
                        full_text = title
                        location_suggestion = extract_location_from_text(full_text)
                        return ScrapeResponse(
                            title=title[:200] if title else None,
                            description=title[:500] if title else None,
                            location_suggestion=location_suggestion
                        )
                except Exception:
                    pass
            
            # Generic HTML scraping (with bot UA for social platforms)
            response = await client.get(resolved_url, headers={'User-Agent': user_agent})
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to get title
            title = None
            og_title = soup.find('meta', property='og:title')
            if og_title:
                title = og_title.get('content')
            if not title:
                twitter_title = soup.find('meta', attrs={'name': 'twitter:title'})
                if twitter_title:
                    title = twitter_title.get('content')
            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.string
            
            # Clean platform prefix from title (e.g. "TikTok · Creator Name")
            if title:
                for prefix in ['TikTok · ', 'TikTok - ', 'Instagram - ']:
                    if title.startswith(prefix):
                        title = title[len(prefix):]
            
            # Try to get description
            description = None
            og_desc = soup.find('meta', property='og:description')
            if og_desc:
                description = og_desc.get('content')
            if not description:
                twitter_desc = soup.find('meta', attrs={'name': 'twitter:description'})
                if twitter_desc:
                    description = twitter_desc.get('content')
            if not description:
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc:
                    description = meta_desc.get('content')
            
            # Extract location from text
            full_text = f"{title or ''} {description or ''}"
            location_suggestion = extract_location_from_text(full_text)
            
            return ScrapeResponse(
                title=title,
                description=description,
                location_suggestion=location_suggestion
            )
    
    except Exception as e:
        logging.error(f"Error scraping URL {url}: {str(e)}")
        return ScrapeResponse(title=None, description=None, location_suggestion=None)


# ============================================================================
# ROUTES
# ============================================================================

@api_router.get("/")
async def root():
    return {"message": "Save Later API"}

# Authentication Routes
@api_router.post("/auth/google", response_model=TokenResponse)
async def google_login(request: GoogleLoginRequest):
    """Verify Google ID token and create/login user"""
    try:
        # For MVP, we'll accept the token without strict verification
        # In production, you should verify with Google's servers
        # For now, we'll decode the token payload (not verified)
        import base64
        import json
        
        # Split the token and decode the payload
        parts = request.id_token.split('.')
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail="Invalid token format")
        
        # Decode payload (add padding if needed)
        payload_part = parts[1]
        padding = 4 - len(payload_part) % 4
        if padding != 4:
            payload_part += '=' * padding
        
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        
        google_id = payload.get('sub')
        email = payload.get('email')
        name = payload.get('name', email.split('@')[0])
        picture = payload.get('picture')
        
        if not google_id or not email:
            raise HTTPException(status_code=400, detail="Invalid token payload")
        
        # Check if user exists
        existing_user = await db.users.find_one({"google_id": google_id})
        
        if existing_user:
            user = User(**existing_user)
        else:
            # Create new user
            user = User(
                google_id=google_id,
                email=email,
                name=name,
                profile_picture=picture
            )
            await db.users.insert_one(user.dict())
        
        # Create JWT token
        access_token = create_access_token(data={"sub": user.id})
        
        return TokenResponse(
            access_token=access_token,
            user={
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "profile_picture": user.profile_picture
            }
        )
    
    except Exception as e:
        logging.error(f"Google login error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")

@api_router.get("/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user info"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "profile_picture": current_user.profile_picture
    }

# Scraping Route
@api_router.post("/scrape", response_model=ScrapeResponse)
async def scrape_url(request: ScrapeRequest, current_user: User = Depends(get_current_user)):
    """Scrape metadata from URL"""
    return await scrape_metadata(request.url)

# Google Places Route
@api_router.post("/places/search", response_model=PlaceSearchResponse)
async def search_places(request: PlaceSearchRequest, current_user: User = Depends(get_current_user)):
    """Search places using Google Places API (uses backend API key)"""
    if not GOOGLE_PLACES_API_KEY:
        raise HTTPException(status_code=500, detail="Google Places API key not configured on server")
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={
                    "query": request.query,
                    "key": GOOGLE_PLACES_API_KEY
                }
            )
            data = response.json()
            
            status = data.get("status")
            if status == "REQUEST_DENIED":
                error_msg = data.get("error_message", "API key not authorized")
                logging.error(f"Google Places API denied: {error_msg}")
                raise HTTPException(status_code=502, detail=f"Google Places API error: {error_msg}")
            
            if status != "OK" and status != "ZERO_RESULTS":
                logging.error(f"Google Places API status: {status}")
                return PlaceSearchResponse(places=[])
            
            places = []
            for result in data.get("results", [])[:5]:  # Limit to 5 results
                places.append({
                    "name": result.get("name"),
                    "address": result.get("formatted_address"),
                    "latitude": result.get("geometry", {}).get("location", {}).get("lat"),
                    "longitude": result.get("geometry", {}).get("location", {}).get("lng"),
                    "place_id": result.get("place_id")
                })
            
            return PlaceSearchResponse(places=places)
    
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Places search error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to search places")

# Location Routes
@api_router.post("/locations", response_model=SavedLocation)
async def create_location(location: LocationCreate, current_user: User = Depends(get_current_user)):
    """Create a new saved location"""
    new_location = SavedLocation(
        user_id=current_user.id,
        **location.dict()
    )
    await db.locations.insert_one(new_location.dict())
    return new_location

@api_router.get("/locations", response_model=List[SavedLocation])
async def get_locations(current_user: User = Depends(get_current_user)):
    """Get all locations for current user"""
    locations = await db.locations.find({"user_id": current_user.id}).to_list(1000)
    return [SavedLocation(**loc) for loc in locations]

@api_router.get("/locations/{location_id}", response_model=SavedLocation)
async def get_location(location_id: str, current_user: User = Depends(get_current_user)):
    """Get a specific location"""
    location = await db.locations.find_one({"id": location_id, "user_id": current_user.id})
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    return SavedLocation(**location)

@api_router.put("/locations/{location_id}", response_model=SavedLocation)
async def update_location(location_id: str, update: LocationUpdate, current_user: User = Depends(get_current_user)):
    """Update a location"""
    location = await db.locations.find_one({"id": location_id, "user_id": current_user.id})
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if update_data:
        await db.locations.update_one({"id": location_id}, {"$set": update_data})
        location.update(update_data)
    
    return SavedLocation(**location)

@api_router.delete("/locations/{location_id}")
async def delete_location(location_id: str, current_user: User = Depends(get_current_user)):
    """Delete a location"""
    result = await db.locations.delete_one({"id": location_id, "user_id": current_user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Location not found")
    
    # Also remove from all lists
    await db.lists.update_many(
        {"user_id": current_user.id},
        {"$pull": {"location_ids": location_id}}
    )
    
    return {"message": "Location deleted"}

# List Routes
@api_router.post("/lists", response_model=ListModel)
async def create_list(list_data: ListCreate, current_user: User = Depends(get_current_user)):
    """Create a new list"""
    new_list = ListModel(
        user_id=current_user.id,
        **list_data.dict()
    )
    await db.lists.insert_one(new_list.dict())
    return new_list

@api_router.get("/lists", response_model=List[ListModel])
async def get_lists(current_user: User = Depends(get_current_user)):
    """Get all lists for current user"""
    lists = await db.lists.find({"user_id": current_user.id}).to_list(1000)
    return [ListModel(**lst) for lst in lists]

@api_router.get("/lists/{list_id}", response_model=ListModel)
async def get_list(list_id: str, current_user: User = Depends(get_current_user)):
    """Get a specific list"""
    list_doc = await db.lists.find_one({"id": list_id, "user_id": current_user.id})
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found")
    return ListModel(**list_doc)

@api_router.put("/lists/{list_id}", response_model=ListModel)
async def update_list(list_id: str, update: ListUpdate, current_user: User = Depends(get_current_user)):
    """Update a list"""
    list_doc = await db.lists.find_one({"id": list_id, "user_id": current_user.id})
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if update_data:
        await db.lists.update_one({"id": list_id}, {"$set": update_data})
        list_doc.update(update_data)
    
    return ListModel(**list_doc)

@api_router.delete("/lists/{list_id}")
async def delete_list(list_id: str, current_user: User = Depends(get_current_user)):
    """Delete a list"""
    result = await db.lists.delete_one({"id": list_id, "user_id": current_user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="List not found")
    return {"message": "List deleted"}

@api_router.post("/lists/{list_id}/locations")
async def add_location_to_list(list_id: str, request: AddLocationToListRequest, current_user: User = Depends(get_current_user)):
    """Add a location to a list"""
    list_doc = await db.lists.find_one({"id": list_id, "user_id": current_user.id})
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found")
    
    # Verify location exists and belongs to user
    location = await db.locations.find_one({"id": request.location_id, "user_id": current_user.id})
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    
    # Add location to list if not already there
    if request.location_id not in list_doc.get("location_ids", []):
        await db.lists.update_one(
            {"id": list_id},
            {"$push": {"location_ids": request.location_id}}
        )
    
    return {"message": "Location added to list"}

@api_router.delete("/lists/{list_id}/locations/{location_id}")
async def remove_location_from_list(list_id: str, location_id: str, current_user: User = Depends(get_current_user)):
    """Remove a location from a list"""
    list_doc = await db.lists.find_one({"id": list_id, "user_id": current_user.id})
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found")
    
    await db.lists.update_one(
        {"id": list_id},
        {"$pull": {"location_ids": location_id}}
    )
    
    return {"message": "Location removed from list"}

@api_router.get("/lists/shared/{share_token}")
async def get_shared_list(share_token: str):
    """Get a shared list (public, no auth required)"""
    list_doc = await db.lists.find_one({"share_token": share_token, "is_public": True})
    if not list_doc:
        raise HTTPException(status_code=404, detail="Shared list not found")
    
    # Get locations in the list
    location_ids = list_doc.get("location_ids", [])
    locations = []
    if location_ids:
        locations = await db.locations.find({"id": {"$in": location_ids}}).to_list(1000)
    
    return {
        "list": ListModel(**list_doc),
        "locations": [SavedLocation(**loc) for loc in locations]
    }

@api_router.get("/lists/shared/{share_token}/page")
async def get_shared_list_page(share_token: str):
    
    list_doc = await db.lists.find_one({"share_token": share_token, "is_public": True})
    if not list_doc:
        return HTMLResponse(content="""
        <html><head><title>Not Found</title><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{font-family:-apple-system,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#F4F1EC;color:#3E3A36;margin:0;}
        .c{text-align:center;padding:32px;}.t{font-size:20px;font-weight:500;margin-bottom:8px;}.s{color:#8A837B;font-size:14px;}</style>
        </head><body><div class="c"><div class="t">List not found</div><div class="s">This list may be private or deleted.</div></div></body></html>
        """, status_code=404)
    
    location_ids = list_doc.get("location_ids", [])
    locations = []
    if location_ids:
        locations = await db.locations.find({"id": {"$in": location_ids}}).to_list(1000)
    
    list_model = ListModel(**list_doc)
    
    # Build location cards HTML
    location_cards = ""
    for loc in locations:
        loc_data = SavedLocation(**loc)
        cat_color = {
            'Food': '#C6B8BE', 'Coffee': '#8A837B', 'Bar': '#B7C1B0',
            'Holiday': '#D6D0C8', 'Activity': '#B7C1B0', 'Shopping': '#C6B8BE', 'Other': '#D6D0C8'
        }.get(loc_data.category, '#D6D0C8')
        
        coords_html = ""
        if loc_data.latitude and loc_data.longitude:
            maps_url = f"https://maps.google.com/?q={loc_data.latitude},{loc_data.longitude}"
            coords_html = f'<a href="{maps_url}" target="_blank" class="coords"><span class="pin">📍</span>{loc_data.latitude:.4f}, {loc_data.longitude:.4f}</a>'
        
        desc_html = f'<div class="desc">{loc_data.description}</div>' if loc_data.description else ""
        link_html = f'<a href="{loc_data.link}" target="_blank" class="link">🔗 Source</a>' if loc_data.link else ""
        
        location_cards += f'''
        <div class="card">
          <div class="card-head">
            <span class="cat" style="background:{cat_color}">{loc_data.category}</span>
            {link_html}
          </div>
          <div class="card-title">{loc_data.title}</div>
          {desc_html}
          {coords_html}
        </div>'''
    
    empty_html = '<div class="empty"><div class="empty-icon">📍</div><div class="empty-text">No places in this list yet</div></div>' if not locations else ""
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{list_model.name} — Save Later</title>
  <meta name="description" content="{list_model.description or f'A curated list of {len(locations)} places'}">
  <meta property="og:title" content="{list_model.name} — Save Later">
  <meta property="og:description" content="{list_model.description or f'A curated list of {len(locations)} places'}">
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F4F1EC;color:#3E3A36;min-height:100vh}}
    .header{{background:#FEFEFC;border-bottom:1px solid #F0EBE5;padding:24px 20px 20px}}
    .brand{{font-size:11px;color:#A49A91;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px;font-weight:500}}
    .title{{font-size:28px;font-weight:500;letter-spacing:-0.5px;color:#3E3A36}}
    .description{{font-size:14px;color:#8A837B;margin-top:6px;line-height:20px}}
    .meta{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}}
    .badge{{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#A49A91;background:#F4F1EC;padding:4px 10px;border-radius:20px;letter-spacing:0.2px}}
    .badge.public{{background:rgba(143,168,134,0.15);color:#8FA886}}
    .content{{padding:16px;max-width:600px;margin:0 auto}}
    .card{{background:#FEFEFC;border-radius:16px;padding:20px;margin-bottom:12px;border:1px solid #F0EBE5;box-shadow:0 2px 8px rgba(62,58,54,0.06)}}
    .card-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
    .cat{{font-size:11px;font-weight:500;color:#3E3A36;text-transform:uppercase;letter-spacing:0.3px;padding:4px 12px;border-radius:20px;border:1px solid rgba(62,58,54,0.08)}}
    .card-title{{font-size:20px;font-weight:500;letter-spacing:-0.3px;line-height:26px;margin-bottom:6px}}
    .desc{{font-size:14px;color:#8A837B;line-height:20px;margin-bottom:8px}}
    .coords{{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#A49A91;background:#F4F1EC;padding:4px 10px;border-radius:8px;text-decoration:none;letter-spacing:0.2px}}
    .coords:hover{{background:#E8E3DD}}
    .pin{{font-size:10px}}
    .link{{font-size:12px;color:#8A837B;text-decoration:none;opacity:0.7}}
    .link:hover{{opacity:1}}
    .empty{{text-align:center;padding:60px 20px}}
    .empty-icon{{font-size:48px;margin-bottom:16px}}
    .empty-text{{font-size:14px;color:#8A837B}}
    .footer{{text-align:center;padding:32px 20px;font-size:11px;color:#A49A91;letter-spacing:0.3px}}
    .footer a{{color:#8A837B;text-decoration:none;font-weight:500}}
  </style>
</head>
<body>
  <div class="header">
    <div class="brand">Save Later</div>
    <div class="title">{list_model.name}</div>
    {"<div class='description'>" + list_model.description + "</div>" if list_model.description else ""}
    <div class="meta">
      <span class="badge">📍 {len(locations)} place{"s" if len(locations) != 1 else ""}</span>
      <span class="badge public">🌐 Public list</span>
    </div>
  </div>
  <div class="content">
    {location_cards}
    {empty_html}
  </div>
  <div class="footer">Shared via <a href="#">Save Later</a> — Your curated place archive</div>
</body>
</html>'''
    
    return HTMLResponse(content=html)

# --- Deep Linking: AASA & Smart Share Page ---
from fastapi.responses import JSONResponse, HTMLResponse

@api_router.get("/aasa")
async def apple_app_site_association():
    """AASA content - proxy from /.well-known/apple-app-site-association on savelater.com.tr"""
    return JSONResponse(content={
        "applinks": {
            "apps": [],
            "details": [{
                "appIDs": ["YCM488GLU5.com.defneeduruu.savelater"],
                "paths": ["/share/*"],
                "components": [{"/" : "/share/*"}]
            }]
        }
    })

@api_router.get("/share/{share_token}")
async def smart_share_page(share_token: str):
    """Smart share page: opens app via Universal Link, falls back to web view"""
    list_doc = await db.lists.find_one({"share_token": share_token, "is_public": True})
    
    list_name = list_doc.get("name", "Shared List") if list_doc else "List Not Found"
    list_desc = list_doc.get("description", "") if list_doc else "This list may be private or deleted."
    loc_count = len(list_doc.get("location_ids", [])) if list_doc else 0
    
    backend_url = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://save-later-build.preview.emergentagent.com")
    fallback_url = f"{backend_url}/api/lists/shared/{share_token}/page"
    app_scheme = f"savelater://share/{share_token}"
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{list_name} — Save Later</title>
  <meta name="description" content="{list_desc or f'A curated list of {loc_count} places'}">
  <meta property="og:title" content="{list_name} — Save Later">
  <meta property="og:description" content="{list_desc or f'A curated list of {loc_count} places'}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://savelater.com.tr/share/{share_token}">
  <meta name="apple-itunes-app" content="app-id=6759986501, app-argument=savelater://share/{share_token}">
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F4F1EC;color:#3E3A36;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:32px 20px}}
    .card{{background:#FEFEFC;border-radius:20px;padding:32px;max-width:400px;width:100%;text-align:center;border:1px solid #F0EBE5;box-shadow:0 4px 24px rgba(62,58,54,0.08)}}
    .brand{{font-size:11px;color:#A49A91;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:20px;font-weight:500}}
    .title{{font-size:24px;font-weight:500;letter-spacing:-0.5px;margin-bottom:8px}}
    .desc{{font-size:14px;color:#8A837B;line-height:20px;margin-bottom:16px}}
    .count{{font-size:12px;color:#A49A91;margin-bottom:24px}}
    .btn{{display:inline-block;background:#3E3A36;color:#FEFEFC;padding:14px 32px;border-radius:30px;text-decoration:none;font-size:15px;font-weight:500;letter-spacing:-0.2px;transition:opacity 0.2s}}
    .btn:hover{{opacity:0.85}}
    .web-link{{display:block;margin-top:16px;font-size:13px;color:#8A837B;text-decoration:none}}
    .web-link:hover{{color:#3E3A36}}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand">Save Later</div>
    <div class="title">{list_name}</div>
    {"<div class='desc'>" + list_desc + "</div>" if list_desc else ""}
    <div class="count">{loc_count} place{"s" if loc_count != 1 else ""}</div>
    <a href="{app_scheme}" class="btn" id="open-app">Open in App</a>
    <a href="{fallback_url}" class="web-link">View in browser</a>
  </div>
  <script>
    var appOpened = false;
    document.getElementById('open-app').addEventListener('click', function(e) {{
      e.preventDefault();
      window.location.href = '{app_scheme}';
      setTimeout(function() {{
        if (!appOpened) window.location.href = '{fallback_url}';
      }}, 2500);
    }});
    window.addEventListener('blur', function() {{ appOpened = true; }});
  </script>
</body>
</html>'''
    return HTMLResponse(content=html)

# --- End Deep Linking ---

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
