# ⚡ Quick Start Guide - ResumeAI

## 🚀 Launch in 2 Minutes

### Option 1: Automatic (Recommended)

**macOS/Linux:**
```bash
cd resume-analyzer
./start.sh
```

**Windows:**
```batch
cd resume-analyzer
start.bat
```

Then open: http://localhost:8000

### Option 2: Manual

**Terminal 1 (Backend):**
```bash
cd resume-analyzer/backend
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY='your-key'  # Windows: set ANTHROPIC_API_KEY=your-key
python app.py
```

**Terminal 2 (Frontend):**
```bash
cd resume-analyzer/frontend
python3 -m http.server 8000
```

Open: http://localhost:8000

---

## 🔑 Get API Key

1. Go to: https://console.anthropic.com/
2. Sign up / Log in
3. Go to "API Keys"
4. Create new key
5. Copy the key (starts with `sk-ant-`)
6. Set it:
   ```bash
   export ANTHROPIC_API_KEY='sk-ant-your-key-here'
   ```

---

## 📁 File Structure

```
resume-analyzer/
├── frontend/
│   └── index.html           ← Main website
├── backend/
│   ├── app.py              ← API server
│   ├── requirements.txt    ← Python packages
│   └── stripe_integration.py ← Payments
├── README.md               ← Full documentation
├── DEPLOYMENT.md           ← How to deploy
├── BUSINESS_PLAN.md        ← Marketing strategy
├── start.sh               ← One-click start (Mac/Linux)
└── start.bat              ← One-click start (Windows)
```

---

## 💡 Key Features

✅ AI-powered resume analysis (Claude 4)
✅ Beautiful modern UI
✅ ATS optimization scoring
✅ Instant feedback (30 seconds)
✅ PDF, DOC, DOCX support
✅ Stripe payment integration ready
✅ Free demo mode included

---

## 🎯 Revenue Model

- **Starter:** $9.99 → 3 analyses
- **Pro:** $29.99/month → Unlimited
- **Enterprise:** $99/month → Teams

**Goal:** 100 users = $1,000-$1,500/month

---

## 📚 Documentation

- **README.md** - Complete setup guide
- **DEPLOYMENT.md** - Deploy to production (Render, Vercel, etc.)
- **BUSINESS_PLAN.md** - Marketing & growth strategy
- **backend/stripe_integration.py** - Payment setup

---

## ✅ Deployment Checklist

1. [ ] Test locally (both frontend & backend running)
2. [ ] Get Anthropic API key
3. [ ] Push code to GitHub
4. [ ] Deploy backend to Render.com
5. [ ] Deploy frontend to Render.com
6. [ ] Set up Stripe for payments
7. [ ] Configure custom domain (optional)
8. [ ] Launch on Product Hunt
9. [ ] Start marketing!

---

## 🐛 Common Issues

**"Module not found"**
→ Run: `pip install -r requirements.txt`

**"CORS error"**
→ Make sure backend is running on port 5000

**"API key invalid"**
→ Check your ANTHROPIC_API_KEY is set correctly

**"File upload fails"**
→ Check file size (<5MB) and format (PDF/DOC/DOCX)

---

## 🎬 Next Steps

1. **Test the app** - Upload a resume and see results
2. **Read DEPLOYMENT.md** - Deploy to production
3. **Read BUSINESS_PLAN.md** - Learn marketing strategy
4. **Set up Stripe** - Start accepting payments
5. **Launch!** - Get your first customers

---

## 📞 Support Resources

- Anthropic Docs: https://docs.anthropic.com/
- Stripe Docs: https://stripe.com/docs
- Flask Docs: https://flask.palletsprojects.com/
- Render Docs: https://render.com/docs

---

## 💰 Cost Estimate

**Development:** $0 (all free tools)
**Hosting:** $0-7/month (Render free tier available)
**API Usage:** ~$15-30/month (for 500 analyses)
**Payment Processing:** 2.9% + 30¢ per transaction

**Total:** $15-40/month to run
**Revenue:** $1,000-2,000/month potential

**Profit Margin:** 85-95% 🎉

---

**You're ready to launch! 🚀**

Questions? Check the full README.md for detailed answers.
