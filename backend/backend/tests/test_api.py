"""
Backend API tests for Save Later app
Tests CRUD operations for locations and lists endpoints
Uses JWT token generation for authenticated endpoints
"""
import pytest
import requests
import os
import uuid
from datetime import datetime, timedelta
from jose import jwt

# Backend URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://save-later-build.preview.emergentagent.com')
if BASE_URL.endswith('/'):
    BASE_URL = BASE_URL.rstrip('/')

# JWT Secret from backend .env
JWT_SECRET_KEY = "save-later-super-secret-key-change-in-production-2025"
ALGORITHM = "HS256"

# Test user ID for all tests
TEST_USER_ID = "test-user-" + str(uuid.uuid4())[:8]


def create_test_token(user_id: str) -> str:
    """Create a valid JWT token for testing"""
    expire = datetime.utcnow() + timedelta(days=1)
    payload = {
        "sub": user_id,
        "exp": expire
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def test_user(api_client):
    """Create a test user in database and return token"""
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio
    
    # For testing, we'll create user directly in MongoDB
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'savelater_production')
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    user_data = {
        "id": TEST_USER_ID,
        "google_id": f"test-google-{TEST_USER_ID}",
        "email": f"test-{TEST_USER_ID}@test.com",
        "name": "Test User",
        "profile_picture": None,
        "created_at": datetime.utcnow()
    }
    
    async def insert_user():
        # Remove existing test user if any
        await db.users.delete_many({"id": TEST_USER_ID})
        await db.users.insert_one(user_data)
    
    asyncio.get_event_loop().run_until_complete(insert_user())
    client.close()
    
    return {
        "user_id": TEST_USER_ID,
        "token": create_test_token(TEST_USER_ID)
    }


@pytest.fixture(scope="module")
def authenticated_client(api_client, test_user):
    """Session with auth header"""
    api_client.headers.update({"Authorization": f"Bearer {test_user['token']}"})
    return api_client


# ============================================================================
# Health Check Tests
# ============================================================================
class TestHealthCheck:
    """Health check and API root endpoint tests"""
    
    def test_api_root_returns_200(self, api_client):
        """Test that API root endpoint is accessible"""
        response = api_client.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["message"] == "Save Later API"
        print(f"✓ API root returned: {data}")


# ============================================================================
# Location CRUD Tests
# ============================================================================
class TestLocationCRUD:
    """Location endpoint CRUD tests"""
    
    created_location_id = None
    
    def test_create_location_returns_full_object(self, authenticated_client):
        """Test POST /api/locations returns full location object with id, title, category"""
        location_data = {
            "title": "TEST_Coffee Shop",
            "description": "A great coffee place",
            "category": "Coffee",
            "link": "https://example.com/coffee",
            "latitude": 41.0082,
            "longitude": 28.9784
        }
        
        response = authenticated_client.post(f"{BASE_URL}/api/locations", json=location_data)
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertions - validate response structure
        data = response.json()
        assert "id" in data, "Response should contain 'id'"
        assert "title" in data, "Response should contain 'title'"
        assert "category" in data, "Response should contain 'category'"
        assert "user_id" in data, "Response should contain 'user_id'"
        
        # Validate field values
        assert data["title"] == location_data["title"]
        assert data["category"] == location_data["category"]
        assert data["description"] == location_data["description"]
        assert data["latitude"] == location_data["latitude"]
        assert data["longitude"] == location_data["longitude"]
        assert isinstance(data["id"], str)
        assert len(data["id"]) > 0
        
        # Store for later tests
        TestLocationCRUD.created_location_id = data["id"]
        print(f"✓ Created location with id: {data['id']}")
    
    def test_get_locations_returns_list(self, authenticated_client):
        """Test GET /api/locations returns list of locations for authenticated user"""
        response = authenticated_client.get(f"{BASE_URL}/api/locations")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), "Response should be a list"
        
        # Should contain at least the location we just created
        if TestLocationCRUD.created_location_id:
            location_ids = [loc["id"] for loc in data]
            assert TestLocationCRUD.created_location_id in location_ids, "Created location should be in list"
        
        print(f"✓ GET /api/locations returned {len(data)} locations")
    
    def test_get_single_location(self, authenticated_client):
        """Test GET /api/locations/{id} returns the specific location"""
        if not TestLocationCRUD.created_location_id:
            pytest.skip("No location created in previous test")
        
        response = authenticated_client.get(f"{BASE_URL}/api/locations/{TestLocationCRUD.created_location_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == TestLocationCRUD.created_location_id
        assert data["title"] == "TEST_Coffee Shop"
        print(f"✓ GET single location returned: {data['title']}")
    
    def test_update_location_returns_updated_object(self, authenticated_client):
        """Test PUT /api/locations/{id} returns updated location object"""
        if not TestLocationCRUD.created_location_id:
            pytest.skip("No location created in previous test")
        
        update_data = {
            "title": "TEST_Updated Coffee Shop",
            "description": "Updated description"
        }
        
        response = authenticated_client.put(
            f"{BASE_URL}/api/locations/{TestLocationCRUD.created_location_id}",
            json=update_data
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify update was applied
        assert data["title"] == update_data["title"]
        assert data["description"] == update_data["description"]
        # Original fields should be preserved
        assert data["category"] == "Coffee"
        
        print(f"✓ Updated location title to: {data['title']}")
    
    def test_get_location_verifies_update_persisted(self, authenticated_client):
        """Test that update was actually persisted in database"""
        if not TestLocationCRUD.created_location_id:
            pytest.skip("No location created in previous test")
        
        response = authenticated_client.get(f"{BASE_URL}/api/locations/{TestLocationCRUD.created_location_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "TEST_Updated Coffee Shop"
        print("✓ Update was persisted in database")
    
    def test_delete_location_returns_success(self, authenticated_client):
        """Test DELETE /api/locations/{id} returns success message"""
        if not TestLocationCRUD.created_location_id:
            pytest.skip("No location created in previous test")
        
        response = authenticated_client.delete(f"{BASE_URL}/api/locations/{TestLocationCRUD.created_location_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted" in data["message"].lower() or "Location deleted" in data["message"]
        
        print(f"✓ Delete returned: {data}")
    
    def test_get_deleted_location_returns_404(self, authenticated_client):
        """Test that deleted location returns 404"""
        if not TestLocationCRUD.created_location_id:
            pytest.skip("No location created in previous test")
        
        response = authenticated_client.get(f"{BASE_URL}/api/locations/{TestLocationCRUD.created_location_id}")
        assert response.status_code == 404
        print("✓ Deleted location correctly returns 404")


# ============================================================================
# List CRUD Tests
# ============================================================================
class TestListCRUD:
    """List endpoint CRUD tests"""
    
    created_list_id = None
    created_share_token = None
    
    def test_create_list_returns_full_object(self, authenticated_client):
        """Test POST /api/lists returns full list object with id, name, share_token"""
        list_data = {
            "name": "TEST_My Favorite Places",
            "description": "Collection of favorite spots",
            "is_public": True
        }
        
        response = authenticated_client.post(f"{BASE_URL}/api/lists", json=list_data)
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertions - validate response structure
        data = response.json()
        assert "id" in data, "Response should contain 'id'"
        assert "name" in data, "Response should contain 'name'"
        assert "share_token" in data, "Response should contain 'share_token'"
        assert "user_id" in data, "Response should contain 'user_id'"
        assert "is_public" in data, "Response should contain 'is_public'"
        assert "location_ids" in data, "Response should contain 'location_ids'"
        
        # Validate field values
        assert data["name"] == list_data["name"]
        assert data["description"] == list_data["description"]
        assert data["is_public"] == list_data["is_public"]
        assert isinstance(data["id"], str)
        assert isinstance(data["share_token"], str)
        assert len(data["id"]) > 0
        assert len(data["share_token"]) > 0
        
        # Store for later tests
        TestListCRUD.created_list_id = data["id"]
        TestListCRUD.created_share_token = data["share_token"]
        print(f"✓ Created list with id: {data['id']}, share_token: {data['share_token'][:8]}...")
    
    def test_get_lists_returns_list(self, authenticated_client):
        """Test GET /api/lists returns list of lists for authenticated user"""
        response = authenticated_client.get(f"{BASE_URL}/api/lists")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), "Response should be a list"
        
        # Should contain at least the list we just created
        if TestListCRUD.created_list_id:
            list_ids = [lst["id"] for lst in data]
            assert TestListCRUD.created_list_id in list_ids, "Created list should be in response"
        
        print(f"✓ GET /api/lists returned {len(data)} lists")
    
    def test_get_single_list(self, authenticated_client):
        """Test GET /api/lists/{id} returns the specific list"""
        if not TestListCRUD.created_list_id:
            pytest.skip("No list created in previous test")
        
        response = authenticated_client.get(f"{BASE_URL}/api/lists/{TestListCRUD.created_list_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == TestListCRUD.created_list_id
        assert data["name"] == "TEST_My Favorite Places"
        print(f"✓ GET single list returned: {data['name']}")
    
    def test_update_list_returns_updated_object(self, authenticated_client):
        """Test PUT /api/lists/{id} returns updated list"""
        if not TestListCRUD.created_list_id:
            pytest.skip("No list created in previous test")
        
        update_data = {
            "name": "TEST_Updated Favorite Places",
            "description": "Updated collection description"
        }
        
        response = authenticated_client.put(
            f"{BASE_URL}/api/lists/{TestListCRUD.created_list_id}",
            json=update_data
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify update was applied
        assert data["name"] == update_data["name"]
        assert data["description"] == update_data["description"]
        # Original fields should be preserved
        assert data["is_public"] == True
        
        print(f"✓ Updated list name to: {data['name']}")
    
    def test_shared_list_public_access(self, api_client):
        """Test GET /api/lists/shared/{share_token} works without auth for public lists"""
        if not TestListCRUD.created_share_token:
            pytest.skip("No list created in previous test")
        
        # Use client without auth header
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        response = session.get(f"{BASE_URL}/api/lists/shared/{TestListCRUD.created_share_token}")
        
        assert response.status_code == 200
        data = response.json()
        assert "list" in data
        assert "locations" in data
        print(f"✓ Public shared list accessible without auth")
    
    def test_delete_list_returns_success(self, authenticated_client):
        """Test DELETE /api/lists/{id} returns success message"""
        if not TestListCRUD.created_list_id:
            pytest.skip("No list created in previous test")
        
        response = authenticated_client.delete(f"{BASE_URL}/api/lists/{TestListCRUD.created_list_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted" in data["message"].lower() or "List deleted" in data["message"]
        
        print(f"✓ Delete returned: {data}")
    
    def test_get_deleted_list_returns_404(self, authenticated_client):
        """Test that deleted list returns 404"""
        if not TestListCRUD.created_list_id:
            pytest.skip("No list created in previous test")
        
        response = authenticated_client.get(f"{BASE_URL}/api/lists/{TestListCRUD.created_list_id}")
        assert response.status_code == 404
        print("✓ Deleted list correctly returns 404")


# ============================================================================
# Authentication Tests
# ============================================================================
class TestAuthentication:
    """Authentication and authorization tests"""
    
    def test_unauthenticated_locations_returns_401(self, api_client):
        """Test that locations endpoint requires auth"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        response = session.get(f"{BASE_URL}/api/locations")
        assert response.status_code == 401
        print("✓ Unauthenticated request correctly returns 401")
    
    def test_invalid_token_returns_401(self, api_client):
        """Test that invalid token is rejected"""
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Authorization": "Bearer invalid-token-here"
        })
        
        response = session.get(f"{BASE_URL}/api/locations")
        assert response.status_code == 401
        print("✓ Invalid token correctly returns 401")
    
    def test_get_me_endpoint(self, authenticated_client):
        """Test GET /api/auth/me returns current user info"""
        response = authenticated_client.get(f"{BASE_URL}/api/auth/me")
        
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "email" in data
        assert "name" in data
        print(f"✓ GET /api/auth/me returned user: {data['email']}")


# ============================================================================
# Cleanup
# ============================================================================
@pytest.fixture(scope="module", autouse=True)
def cleanup(request, api_client):
    """Cleanup test data after all tests"""
    yield
    
    # Cleanup test user and associated data
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        import asyncio
        
        mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
        db_name = os.environ.get('DB_NAME', 'savelater_production')
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        async def cleanup_data():
            # Delete test locations
            result = await db.locations.delete_many({"title": {"$regex": "^TEST_"}})
            print(f"Cleaned up {result.deleted_count} test locations")
            
            # Delete test lists
            result = await db.lists.delete_many({"name": {"$regex": "^TEST_"}})
            print(f"Cleaned up {result.deleted_count} test lists")
            
            # Delete test user
            result = await db.users.delete_many({"id": TEST_USER_ID})
            print(f"Cleaned up {result.deleted_count} test users")
        
        asyncio.get_event_loop().run_until_complete(cleanup_data())
        client.close()
        print("✓ Test cleanup completed")
    except Exception as e:
        print(f"Warning: Cleanup failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
