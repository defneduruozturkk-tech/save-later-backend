# Railway Deployment Guide for Save Later Backend

## Prerequisites
- Railway account (sign up at https://railway.app)
- GitHub account (for code deployment)

## Step-by-Step Deployment

### 1. Prepare the Code
✅ All configuration files are already created:
- `Procfile` - Process file for deployment
- `railway.json` - Railway configuration
- `nixpacks.toml` - Build configuration
- `requirements.txt` - Python dependencies

### 2. Create Railway Project

1. Go to https://railway.app
2. Click **"Start a New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your GitHub
5. Select your repository (or push this code to GitHub first)
6. Select the `/backend` directory as the root

### 3. Add MongoDB Database

1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"Add MongoDB"**
3. Railway will automatically create a MongoDB instance
4. Copy the `MONGO_URL` connection string from the MongoDB service variables

### 4. Configure Environment Variables

In your Railway backend service, go to **"Variables"** and add:

```
MONGO_URL=<paste the MongoDB URL from step 3>
DB_NAME=savelater_production
JWT_SECRET_KEY=66OJZbgw8HwNkn8LLlpJCAkyLX_dxIPZhJE8_9pG0HFktQkuLvnyNPwmb-m4k1TKwERi6Guk5StDe_TLP9rJ5g
GOOGLE_PLACES_API_KEY=AIzaSyC-ECfslDf6RFm4sPNITrmP7gOZ7GRxTUo
GOOGLE_MAPS_API_KEY=AIzaSyB-hb2WZH7Tgpd5lK4-Gv2uSdzIzDcovH8
ENVIRONMENT=production
CORS_ORIGINS=*
```

### 5. Deploy

1. Railway will automatically deploy after you add the variables
2. Wait for the deployment to complete (~2-3 minutes)
3. Click on your backend service → **"Settings"** → **"Domains"**
4. Click **"Generate Domain"** to get your public URL
5. Your backend will be available at: `https://your-service-name.up.railway.app`

### 6. Test the Deployment

Test your deployed backend:
```bash
curl https://your-service-name.up.railway.app/api/
```

You should see: `{"message":"Save Later API"}`

### 7. Update Frontend Configuration

After deployment, update your frontend `eas.json` with the Railway URL:

```json
{
  "build": {
    "production": {
      "env": {
        "EXPO_PUBLIC_BACKEND_URL": "https://your-service-name.up.railway.app"
      }
    }
  }
}
```

Then rebuild your iOS app with the new backend URL.

## Monitoring

- **Logs**: View real-time logs in Railway dashboard
- **Metrics**: Railway provides CPU, memory, and network metrics
- **Alerts**: Set up notifications for deployment failures

## Costs

- **Free Tier**: $5 worth of usage per month (plenty for MVP testing)
- **Pro Plan**: $20/month for more resources if needed
- MongoDB will use ~$1-2/month for small apps

## Troubleshooting

### Build Fails
- Check the build logs in Railway dashboard
- Ensure all environment variables are set
- Verify `requirements.txt` is valid

### App Crashes
- Check the deployment logs
- Verify MongoDB connection string is correct
- Ensure all required environment variables are set

### Can't Connect from App
- Verify the domain is generated and active
- Test the `/api/` endpoint with curl
- Check CORS settings in backend

## Alternative: Deploy via CLI

If you prefer CLI deployment:

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link project
railway link

# Deploy
railway up
```
