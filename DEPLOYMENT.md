# 🚀 Deployment Guide - Render.com (Free Tier)

Render.com offers FREE hosting for web services and static sites. Perfect for launching your MVP!

## Prerequisites

1. GitHub account
2. Render account (sign up at https://render.com)
3. Your code pushed to GitHub

---

## Step 1: Push Code to GitHub

```bash
cd resume-analyzer/

# Initialize git repository
git init

# Create .gitignore
cat > .gitignore << EOF
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
env/
.env
*.db
uploads/
.DS_Store
EOF

# Add all files
git add .

# Commit
git commit -m "Initial commit - ResumeAI"

# Create repository on GitHub
# Then connect and push:
git remote add origin https://github.com/yourusername/resume-ai.git
git branch -M main
git push -u origin main
```

---

## Step 2: Deploy Backend to Render

### A. Create Web Service

1. Go to https://dashboard.render.com
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Select `resume-analyzer` repo

### B. Configure Service

**Basic Settings:**
- Name: `resume-ai-backend`
- Region: Choose closest to your users
- Branch: `main`
- Root Directory: `backend`

**Build Settings:**
- Runtime: `Python 3`
- Build Command:
  ```bash
  pip install -r requirements.txt
  ```

- Start Command:
  ```bash
  gunicorn app:app
  ```

**Environment Variables:**
Click "Advanced" → Add Environment Variable:
- Key: `ANTHROPIC_API_KEY`
- Value: `your-api-key-here`

**Instance Type:**
- Select "Free" (0 GB RAM, shared CPU)

### C. Add gunicorn to requirements.txt

Before deploying, add to `backend/requirements.txt`:
```
gunicorn==21.2.0
```

Then commit and push:
```bash
git add backend/requirements.txt
git commit -m "Add gunicorn for production"
git push
```

### D. Deploy

1. Click "Create Web Service"
2. Wait 3-5 minutes for deployment
3. Copy your backend URL: `https://resume-ai-backend.https://resumeai-2-uaul.onrender.com`

**Note:** Free tier services spin down after 15 minutes of inactivity. First request after inactivity takes 30-60 seconds to wake up.

---

## Step 3: Deploy Frontend to Render

### A. Create Static Site

1. Go to Render Dashboard
2. Click "New +" → "Static Site"
3. Select same GitHub repository

### B. Configure Static Site

**Basic Settings:**
- Name: `resume-ai-frontend`
- Branch: `main`
- Root Directory: `frontend`

**Build Settings:**
- Build Command: (leave empty)
- Publish Directory: `.`

**Headers (Optional but recommended):**
Add in "Settings" → "Headers":
```
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  X-XSS-Protection: 1; mode=block
```

### C. Update API URL in Frontend

Before deploying frontend, update the API endpoint in `frontend/index.html`:

Find this line (around line 580):
```javascript
const response = await fetch('http://localhost:5000/api/analyze', {
```

Replace with:
```javascript
const response = await fetch('https://resume-ai-backend.https://resumeai-2-uaul.onrender.com/api/analyze', {
```

Commit and push:
```bash
git add frontend/index.html
git commit -m "Update API URL for production"
git push
```

### D. Deploy

1. Click "Create Static Site"
2. Wait 2-3 minutes
3. Your site will be live at: `https://resume-ai-frontend.https://resumeai-2-uaul.onrender.com`

---

## Step 4: Custom Domain (Optional)

### A. Add Custom Domain to Render

1. Go to your Static Site settings
2. Click "Custom Domains"
3. Click "Add Custom Domain"
4. Enter your domain: `resumeai.com`

### B. Configure DNS

In your domain registrar (Namecheap, GoDaddy, etc.):

**For root domain (resumeai.com):**
- Type: `A`
- Name: `@`
- Value: Render's IP (provided in dashboard)

**For www subdomain:**
- Type: `CNAME`
- Name: `www`
- Value: `your-site.https://resumeai-2-uaul.onrender.com`

DNS propagation takes 1-24 hours.

---

## Step 5: Environment Variables & Secrets

### Required Environment Variables

In Render Dashboard → Your Backend Service → Environment:

```bash
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Flask
FLASK_ENV=production

# Stripe (when ready)
STRIPE_SECRET_KEY=sk_live_your-key-here
STRIPE_WEBHOOK_SECRET=whsec_your-webhook-secret

# Optional: Email
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

### Auto-Deploy on Git Push

Render automatically deploys when you push to GitHub!

```bash
# Make changes
git add .
git commit -m "Update feature"
git push

# Render will automatically rebuild and redeploy
```

---

## Step 6: Set Up Database (Persistent Storage)

The free tier doesn't have persistent disk storage. For production database:

### Option A: Use Render PostgreSQL (Free)

1. Create PostgreSQL database on Render
2. Update `app.py` to use PostgreSQL instead of SQLite:

```python
import psycopg2
import os

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)
```

3. Add to `requirements.txt`:
```
psycopg2-binary==2.9.9
```

### Option B: Use External Database

Free database options:
- **Supabase** (PostgreSQL): https://supabase.com
- **PlanetScale** (MySQL): https://planetscale.com
- **MongoDB Atlas**: https://www.mongodb.com/cloud/atlas

---

## Step 7: Monitoring & Logs

### View Logs

In Render Dashboard:
1. Go to your service
2. Click "Logs" tab
3. See real-time logs

### Set Up Alerts

1. Go to service settings
2. Enable "Deploy Notifications"
3. Add your email

### Add Health Checks

Render automatically pings `/` endpoint. For custom health check:

In `app.py`, add:
```python
@app.route('/health')
def health():
    return {'status': 'healthy'}, 200
```

Configure in Render:
- Path: `/health`
- Interval: 60 seconds

---

## Step 8: Performance Optimization

### Enable Caching

Add to `app.py`:
```python
from flask import make_response

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response
```

### Compress Responses

```python
from flask_compress import Compress

compress = Compress()
compress.init_app(app)
```

Add to `requirements.txt`:
```
Flask-Compress==1.14
```

### Use CDN for Static Assets

For images, CSS, JS:
1. Upload to Cloudflare CDN (free)
2. Update URLs in HTML

---

## Step 9: SSL/HTTPS

**Good news:** Render provides free SSL certificates automatically!

Your sites will be:
- `https://resume-ai-backend.https://resumeai-2-uaul.onrender.com` ✅
- `https://resume-ai-frontend.https://resumeai-2-uaul.onrender.com` ✅

For custom domain, SSL is also automatic once DNS is configured.

---

## Troubleshooting

### Backend Won't Start

**Check logs for:**
- Missing dependencies → Update `requirements.txt`
- Wrong Python version → Specify in `runtime.txt`:
  ```
  python-3.11.5
  ```
- Environment variables not set → Check dashboard

### CORS Errors

Update `app.py`:
```python
CORS(app, origins=['https://resume-ai-frontend.https://resumeai-2-uaul.onrender.com'])
```

### Database Errors

Free tier resets database on each deploy. Use persistent database for production.

### Slow Response Times

Free tier sleeps after inactivity. Upgrade to paid tier ($7/mo) for always-on service.

---

## Cost Breakdown

### Free Tier (MVP):
- Backend: Free (sleeps after 15 min)
- Frontend: Free
- PostgreSQL: Free (limited)
- SSL: Free
- **Total: $0/month** ✅

### Starter Tier (Production):
- Backend: $7/mo (always on)
- Frontend: Free
- PostgreSQL: Free → $7/mo for 1GB
- **Total: $7-14/month**

### Growth Tier:
- Backend: $25/mo (more resources)
- Database: $25/mo (10GB)
- CDN: $10/mo
- **Total: $60/month**

---

## Deployment Checklist

Before going live:

- [ ] Backend deployed and running
- [ ] Frontend deployed and running
- [ ] Environment variables set
- [ ] API endpoints updated
- [ ] Database configured
- [ ] Stripe webhooks configured
- [ ] Custom domain configured (optional)
- [ ] SSL certificate active
- [ ] Google Analytics added
- [ ] Error tracking (Sentry) added
- [ ] Tested all features in production
- [ ] Backup strategy in place

---

## Alternative Hosting Options

If Render doesn't work for you:

### Vercel (Best for Frontend)
- Free static hosting
- Best performance
- Easy GitHub integration
- Deploy backend elsewhere

### Railway (Good for Backend)
- $5/mo for basic plan
- PostgreSQL included
- Simple deployment

### Fly.io (Good for Global)
- Free tier available
- Edge deployment
- Great for worldwide users

### Heroku (Classic)
- No free tier anymore
- $7/mo minimum
- Easy to use

---

## Next Steps

After deployment:

1. **Test everything** in production
2. **Set up monitoring** (UptimeRobot)
3. **Configure backups** for database
4. **Add analytics** (Google Analytics)
5. **Set up error tracking** (Sentry)
6. **Launch marketing** campaign
7. **Collect feedback** from users

---

## Support

**Render Help:**
- Docs: https://render.com/docs
- Community: https://community.render.com

**Need help?**
- Check logs first
- Search Render community
- Review this guide
- Test locally first

---

**Your app is now live! 🎉**

Share your URL:
- `https://resume-ai-frontend.https://resumeai-2-uaul.onrender.com`

Time to get users! 🚀
