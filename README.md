# 🚀 ResumeAI - Complete Setup Guide

## 📋 Overview

ResumeAI is an AI-powered resume analysis platform that helps job seekers improve their resumes using Claude AI. The platform analyzes resumes for ATS compatibility, content quality, and provides actionable recommendations.

**Revenue Model:**
- Starter: $9.99 for 3 analyses
- Professional: $29.99/month (unlimited)
- Enterprise: $99/month (teams)

**Target**: 100-150 paying users = $1,000-$1,500/month

---

## 🛠️ Project Structure

```
resume-analyzer/
├── frontend/
│   └── index.html          # Landing page & app UI
├── backend/
│   ├── app.py             # Flask API server
│   ├── requirements.txt   # Python dependencies
│   └── uploads/           # Temporary file storage
├── config/
│   └── .env.example       # Environment variables template
└── README.md              # This file
```

---

## 📦 Prerequisites

1. **Python 3.9+**
2. **Node.js** (optional, for advanced deployment)
3. **Anthropic API Key** - Get it from: https://console.anthropic.com/
4. **Stripe Account** (for payments) - https://stripe.com/

---

## 🚀 Quick Start (5 minutes)

### Step 1: Set Up Backend

```bash
# Navigate to backend folder
cd backend/

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set your Anthropic API key
export ANTHROPIC_API_KEY='your-api-key-here'
# On Windows:
set ANTHROPIC_API_KEY=your-api-key-here

# Run the server
python app.py
```

The backend should now be running at `http://localhost:5000`

### Step 2: Launch Frontend

```bash
# In a new terminal, navigate to frontend folder
cd frontend/

# Option 1: Simple Python server
python3 -m http.server 8000

# Option 2: Using Node.js (if installed)
npx serve .

# Option 3: Just open index.html in your browser
# File -> Open -> select index.html
```

Visit `http://localhost:8000` in your browser!

---

## 🌐 Deployment to Production

### Option 1: Deploy to Render (Recommended - FREE)

**Backend:**
1. Push code to GitHub
2. Go to https://render.com
3. Create new "Web Service"
4. Connect your GitHub repo
5. Set these configurations:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python app.py`
   - Add Environment Variable: `ANTHROPIC_API_KEY`
6. Deploy!

**Frontend:**
1. On Render, create new "Static Site"
2. Set Build Command: (leave empty)
3. Set Publish Directory: `frontend`
4. Update `index.html` to use your backend URL (line 580):
   ```javascript
   const response = await fetch('https://your-backend.onrender.com/api/analyze', {
   ```

### Option 2: Deploy to Vercel

**Frontend:**
```bash
cd frontend/
npx vercel --prod
```

**Backend:**
Use Render, Railway, or Heroku for Python backend

### Option 3: Deploy to Heroku

```bash
# Install Heroku CLI first
heroku login
cd backend/
heroku create your-app-name
git init
git add .
git commit -m "Initial commit"
git push heroku main
heroku config:set ANTHROPIC_API_KEY=your-key-here
```

---

## 💳 Adding Stripe Payments

### 1. Get Stripe Keys
- Sign up at https://stripe.com
- Get your API keys from Dashboard

### 2. Update Backend

Add to `app.py`:

```python
import stripe

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

@app.route('/api/create-checkout', methods=['POST'])
def create_checkout():
    data = request.json
    plan = data.get('plan')
    
    prices = {
        'starter': 'price_xxx',  # Create these in Stripe Dashboard
        'pro': 'price_xxx',
        'enterprise': 'price_xxx'
    }
    
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price': prices[plan],
            'quantity': 1,
        }],
        mode='subscription' if plan != 'starter' else 'payment',
        success_url='https://yoursite.com/success',
        cancel_url='https://yoursite.com/cancel',
    )
    
    return jsonify({'url': session.url})
```

### 3. Update Frontend

Replace `selectPlan` function in `index.html`:

```javascript
async function selectPlan(plan) {
    const response = await fetch('http://localhost:5000/api/create-checkout', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plan})
    });
    const data = await response.json();
    window.location.href = data.url;
}
```

---

## 📊 Marketing & Growth Strategy

### Launch Checklist:

1. **Product Hunt Launch**
   - Submit your product
   - Offer launch discount (20% off)
   
2. **Social Media**
   - Post on LinkedIn, Twitter
   - Share in job seeker communities
   - Reddit: r/resumes, r/jobs
   
3. **SEO & Content**
   - Blog: "10 Resume Mistakes That Cost You Interviews"
   - YouTube: "How to Pass ATS Systems"
   
4. **Partnerships**
   - Career coaches
   - University career centers
   - Job boards
   
5. **Free Tier Strategy**
   - Offer 1 free analysis
   - Upsell to paid plans

### Pricing Psychology:
- Starter ($9.99): Low barrier to entry
- Professional ($29.99): Sweetspot for job seekers (2-3 analyses per application cycle)
- Enterprise ($99): B2B (recruiting firms, career centers)

---

## 🔧 Configuration Options

### Environment Variables

Create `.env` file in backend folder:

```bash
ANTHROPIC_API_KEY=your_api_key_here
STRIPE_SECRET_KEY=your_stripe_key
FLASK_ENV=production
MAX_FILE_SIZE=5242880
ALLOWED_ORIGINS=https://yourfrontend.com
```

### Customization

**Change Branding:**
- Edit colors in CSS variables (line 13-22 in index.html)
- Update logo text (line 167)
- Modify pricing tiers (line 367-418)

**Adjust AI Analysis:**
- Modify prompt in `analyze_resume_with_claude()` function
- Add more scoring categories
- Customize feedback format

---

## 📈 Monitoring & Analytics

### Add Google Analytics:

Add to `<head>` in index.html:

```html
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-XXXXXXXXXX');
</script>
```

### Track Key Metrics:
- Upload conversion rate
- Plan selection rate
- Average score
- User retention

---

## 🐛 Troubleshooting

**Problem: "CORS error"**
```bash
# Make sure backend is running
# Check CORS settings in app.py
# Update frontend fetch URL to match backend
```

**Problem: "API key invalid"**
```bash
# Verify your Anthropic API key
echo $ANTHROPIC_API_KEY
# Get new key at: https://console.anthropic.com/
```

**Problem: "File upload fails"**
```bash
# Check file size (max 5MB)
# Ensure uploads/ folder exists
# Verify file type (PDF, DOC, DOCX only)
```

**Problem: "Claude returns empty response"**
```python
# The app includes fallback demo analysis
# Check your API credits at Anthropic console
# Verify API key has correct permissions
```

---

## 💰 Cost Estimation

### Monthly Costs (for 100 users):

| Service | Cost |
|---------|------|
| Anthropic API (500 analyses/month) | ~$15-30 |
| Hosting (Render/Vercel) | $0-25 |
| Stripe fees (2.9% + 30¢) | ~$30 |
| **Total** | **$45-85** |

**Revenue** (100 users, 50% on Pro plan):
- 50 Pro users × $29.99 = $1,499.50
- 25 Starter purchases × $9.99 = $249.75
- **Total: ~$1,750/month**

**Profit: $1,665 - $1,705/month** 🎉

---

## 🚀 Next Features to Add

1. **Cover Letter Analysis** - Analyze cover letters too
2. **LinkedIn Profile Review** - Expand to LinkedIn
3. **Interview Prep** - AI mock interviews
4. **Resume Templates** - Downloadable templates
5. **Chrome Extension** - Quick analysis from any job board
6. **Job Matching** - Match resume to job descriptions
7. **API for Developers** - B2B revenue stream

---

## 📞 Support

**Issues?** 
- Check the troubleshooting section above
- Review backend logs: `tail -f backend/app.log`
- Test API: `curl http://localhost:5000/api/health`

**Want to extend this?**
- Add more analysis features
- Integrate with job boards
- Build mobile app
- Add team features

---

## 📄 License & Usage

This is a startup template. Feel free to:
- ✅ Use for commercial purposes
- ✅ Modify and rebrand
- ✅ Deploy and monetize
- ⚠️ Respect Anthropic's API terms of service
- ⚠️ Comply with data privacy laws (GDPR, etc.)

---

## 🎯 Success Metrics

**Week 1 Goal:**
- 10 signups
- 5 paying customers
- $50 revenue

**Month 1 Goal:**
- 100 signups  
- 50 paying customers
- $1,000 revenue

**Month 3 Goal:**
- 500 signups
- 200 paying customers
- $4,000 revenue

---

## 🔥 Final Tips

1. **Launch Fast** - Get it out there, iterate based on feedback
2. **Talk to Users** - Every piece of feedback is gold
3. **Focus on Value** - Does it actually help people get jobs?
4. **Price Confidently** - $29.99/month is reasonable for career advancement
5. **Build in Public** - Share your journey on Twitter/LinkedIn

---

**You now have everything you need to launch a $1,000/month SaaS!**

Good luck! 🚀

---

## Quick Command Reference

```bash
# Start backend
cd backend && source venv/bin/activate && python app.py

# Start frontend  
cd frontend && python3 -m http.server 8000

# Install dependencies
cd backend && pip install -r requirements.txt

# Check if server is running
curl http://localhost:5000/api/health

# View logs
tail -f backend/app.log
```
