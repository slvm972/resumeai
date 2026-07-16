# 🧠 ResumeAI

ResumeAI is a Flask web app that analyzes and improves resumes using AI
(Groq API, `llama-3.3-70b-versatile` with `llama-3.1-8b-instant` as a
429-fallback). It supports multiple languages (English, Russian,
Ukrainian, Hebrew, Arabic, Chinese) via `langid` language detection.

> This README describes the project as it actually exists in the repo
> today — a single Flask application, not the old `frontend/` +
> `backend/` layout described in some of the other docs in this repo
> (`DEPLOYMENT.md`, `BUSINESS_PLAN.md`, `PROJECT_OVERVIEW.md`, etc.).
> Those still reference the legacy structure and are out of date.

---

## 🛠️ Project Structure

```
resumeai/
├── run.py                  # Entry point — python run.py
├── config.py                # Config classes (Development/Production/Testing)
├── Procfile                  # gunicorn run:app  (for Render/Heroku-style hosts)
├── requirements.txt         # Python dependencies
│
├── app/                       # Flask application package
│   ├── __init__.py            # App factory (create_app), legacy routes, page routes
│   ├── missing_routes4.py     # Main LLM "improve resume" pipeline (admin + legacy)
│   ├── models/                 # SQLAlchemy models
│   │   ├── user.py
│   │   ├── subscription.py
│   │   ├── subscription_plan.py
│   │   ├── usage_log.py
│   │   ├── payment.py
│   │   ├── transaction.py
│   │   └── api_key.py
│   ├── routes/                 # JWT-based API blueprints
│   │   ├── auth.py             # /auth/register, /auth/login, /auth/refresh, /auth/me
│   │   ├── user.py             # /users/profile, /users/usage
│   │   ├── subscription.py     # /subscription/plans, /subscription/current, /subscription/cancel
│   │   ├── api_keys.py         # /api-keys (CRUD for user-supplied provider keys)
│   │   └── analysis.py         # /analysis/analyze, /analysis/improve
│   ├── services/                # Business logic
│   │   ├── auth_service.py
│   │   ├── jwt_service.py
│   │   ├── email_service.py
│   │   ├── api_key_service.py
│   │   └── openrouter_service.py   # Groq API calls (analysis + improve)
│   ├── utils/                    # Shared helpers
│   │   ├── constants.py          # Subscription plan definitions
│   │   ├── decorators.py         # @require_active_user, @require_admin, @json_required
│   │   ├── validators.py
│   │   ├── response.py           # success_response / error_response helpers
│   │   └── errors.py             # APIError + Flask error handlers
│   └── tasks/                    # Celery scaffolding (not currently wired into the app)
│       ├── celery_config.py
│       └── api_key_tasks.py
│
├── alembic/, alembic.ini      # DB migrations
├── tests/                     # pytest test suite
├── batch_runner.py            # External HTTP-based regression runner for the improve pipeline
├── static/                    # Static assets (favicon, etc.)
├── uploads/                   # Scratch folder for uploaded files
│
└── Frontend (static HTML, served directly from the repo root):
    ├── index.html              # Landing page + resume upload/analyze UI
    ├── login.html              # User login / register
    ├── dashboard.html          # User dashboard
    ├── history.html            # Analysis history
    ├── admin.html               # Admin panel (analyze/improve without quota)
    └── admin-login.html         # Admin login
```

There is **no separate frontend build step and no `frontend/`/`backend/`
split**. The HTML pages live in the repo root and are served directly by
Flask via `send_from_directory` in `app/__init__.py`, for example:

| URL | File served |
|---|---|
| `/` | `index.html` |
| `/login`, `/login.html` | `login.html` |
| `/dashboard`, `/dashboard.html` | `dashboard.html` |
| `/history`, `/history.html` | `history.html` |
| `/admin` | `admin.html` |
| `/admin-login.html` | `admin-login.html` (falls back to `admin.html` if missing) |

The app also exposes two parallel auth/API systems side by side:

- **JWT-based blueprints** (`app/routes/*`) — the "modern" API, e.g.
  `POST /auth/login`, `POST /analysis/analyze`.
- **Legacy session-based routes**, registered directly in
  `app/__init__.py` and `app/missing_routes4.py` — e.g. `POST /api/login`,
  `POST /api/analyze`, `POST /api/improve`, `POST /api/admin/analyze`,
  `POST /api/admin/improve`, `POST /api/admin/improve/docx`. These are
  what the current HTML pages (`index.html`, `admin.html`) actually call.

---

## 📦 Prerequisites

- **Python 3.9+**
- A **Groq API key** (free tier: 14,400 requests/day) —
  https://console.groq.com/
- A database — SQLite works out of the box for local development;
  PostgreSQL is expected in production (`psycopg2-binary` is in
  `requirements.txt`).

---

## 🔧 Environment Variables

These are read in `config.py`. Variables marked **required** have no
safe default and should always be set outside of local testing;
variables marked **has default** will fall back to a value that is
**not safe for production** unless overridden.

| Variable | Purpose | Status |
|---|---|---|
| `SECRET_KEY` | Flask session/signing secret | **Required in production** — `create_app()` refuses to start in production with the insecure default |
| `JWT_SECRET_KEY` | JWT signing secret | **Required in production** — same startup guard as `SECRET_KEY` |
| `GROQ_API_KEY` | Groq API key used for resume analysis and improvement | **Required** — analysis/improve calls fail without it |
| `ADMIN_EMAIL` | Admin account identifier | Has default (`admin@resumeai.com`) |
| `ADMIN_PASSWORD_HASH` | bcrypt hash of the admin password (not the plaintext password) | **Required** for the hardened admin login path |
| `DATABASE_URL` | SQLAlchemy database URI | Has default — falls back to local SQLite (`sqlite:///resume_analyzer.db`) |
| `FLASK_ENV` | `development` / `production` / `testing` | Has default — `production` (both in `config.py` and in `create_app()`) |
| `ADMIN_MODE` | If `true`, bypasses payment/quota checks | Has default (`false`) — automatically disabled in production regardless of this value |
| `JWT_ACCESS_TOKEN_EXPIRES` / `JWT_REFRESH_TOKEN_EXPIRES` | Token lifetimes in seconds | Has defaults (1 day / 30 days) |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | Has default (localhost variants) |
| `SERVER_URL` / `FRONTEND_URL` | Used for building links (e.g. password reset emails) | Has defaults (`http://localhost:5000`) |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` | Outgoing email (welcome/reset emails) | Have defaults; `MAIL_USERNAME`/`MAIL_PASSWORD` are empty unless set |
| `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_DEFAULT_MODEL` | Alternate/unused AI providers, present in config for future use | Optional |
| `STRIPE_API_KEY`, `STRIPE_PUBLIC_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID`, `STRIPE_ENTERPRISE_PRICE_ID` | Stripe payments — declared in config but not wired into the live payment flow (the current HTML pages link out to Lemon Squeezy checkout instead) | Optional |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_MODE`, `PAYPAL_WEBHOOK_ID` | PayPal payments — same status as Stripe above | Optional |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Celery task queue — `app/tasks/` exists but is not currently invoked by the running app | Optional |

Create a `.env` file in the project root (loaded by `run.py` via
`python-dotenv`), for example:

```bash
SECRET_KEY=replace-with-a-long-random-string
JWT_SECRET_KEY=replace-with-another-long-random-string
GROQ_API_KEY=gsk_your_groq_key_here
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD_HASH=$2b$12$replace-with-a-bcrypt-hash
DATABASE_URL=sqlite:///resume_analyzer.db
FLASK_ENV=development
```

To generate a bcrypt hash for `ADMIN_PASSWORD_HASH`:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'your-admin-password', bcrypt.gensalt()).decode())"
```

---

## 🚀 Quick Start (local development)

```bash
# 1. Clone the repo
git clone https://github.com/slvm972/resumeai.git
cd resumeai

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file (see "Environment Variables" above)
cp .env.example .env            # if you keep an example file locally
# ...then edit .env with your real values

# 5. Run the app
python run.py
```

By default this starts the server at `http://localhost:5000` using
`FLASK_ENV=development` (set `FLASK_ENV=production` to exercise the
production startup guards and defaults). Open `http://localhost:5000`
in your browser — Flask serves `index.html` directly from the repo
root.

To run with debug mode / auto-reload:

```bash
FLASK_DEBUG=1 python run.py
```

`run.py` also prints the resolved port and a truncated `DATABASE_URL`
on startup so you can confirm which database it connected to.

---

## 🩺 Health Check

```bash
curl http://localhost:5000/health
```

---

## 🚢 Production

Production is intended to run under a WSGI server, per the `Procfile`:

```
web: gunicorn run:app --bind 0.0.0.0:$PORT
```

The app has been deployed against Render's free tier, which has real
constraints worth knowing about:

- **Cold starts** — free-tier services sleep after inactivity; the
  first request after a period of inactivity will be slow.
- **Dependency size** — keep `requirements.txt` lean; `requirements_full.txt`
  contains additional packages (Stripe, PayPal, Celery, Redis, PostgreSQL
  driver) meant to be installed separately once needed, not by default.
- In production, `create_app()` will refuse to start if `SECRET_KEY` or
  `JWT_SECRET_KEY` are left at their insecure defaults — make sure both
  are set as real environment variables on your host.

---

## 💳 Subscription Plans

The plan definitions currently live in `app/utils/constants.py`
(`SUBSCRIPTION_PLANS`), and are what the `/subscription/plans` API
endpoint returns:

| Plan | Price | Analyses | Improvements | Custom API key |
|---|---|---|---|---|
| Free | $0 | 2/month | 0 | No |
| Pro | $19.99/month | Unlimited | 50/month | No |
| Enterprise | $9.99/month | Unlimited | Unlimited | Yes |

> ⚠️ **Note on the numbers above:** they are copied exactly from
> `app/utils/constants.py` as it exists in the repo today. Enterprise
> is currently priced *below* Pro despite offering strictly more
> (unlimited improvements + custom API key support) — this looks like
> a data bug rather than an intentional pricing decision, but this
> README doesn't attempt to guess the "correct" number. If you're
> reading this to plan pricing work, treat it as something to confirm
> with the project owner before shipping.

Separately, the static landing page (`index.html`) advertises a
different set of tiers and prices (Starter $9.99 one-time / Professional
$29.99 per month / Enterprise $99 per month) with checkout links that go
to Lemon Squeezy rather than Stripe or PayPal. That marketing copy and
the `SUBSCRIPTION_PLANS` constants are **not currently the same source
of truth** — worth reconciling before relying on either as authoritative.

---

## 🧪 Tests

```bash
python -m pytest tests/test_missing_routes4.py -v
```

There's also `test_config.py` covering configuration defaults, and
`batch_runner.py`, an external HTTP-based regression script that drives
the running server (`analyze` → `improve` → `improve/docx`) against a
folder of sample `.docx` resumes:

```bash
python batch_runner.py --input ./test_resumes --output ./batch_results --base-url http://127.0.0.1:5000
```

---

## 🐛 Troubleshooting

**"GROQ_API_KEY not configured"**
Set `GROQ_API_KEY` in your `.env` file or environment before starting
the server.

**App refuses to start in production**
Check that `SECRET_KEY` and `JWT_SECRET_KEY` are set to real values —
`create_app()` intentionally raises a `RuntimeError` in production if
either is left at its insecure default.

**Admin login fails**
Make sure both `ADMIN_EMAIL` and `ADMIN_PASSWORD_HASH` are set, and
that `ADMIN_PASSWORD_HASH` is a bcrypt hash (not the plaintext
password) — see the snippet under "Environment Variables" above for
how to generate one.

**Database errors on startup**
`db.create_all()` runs inside `create_app()`, so tables are created
automatically against whatever `DATABASE_URL` points to. If you're
switching from SQLite to PostgreSQL, make sure `DATABASE_URL` and the
`psycopg2-binary` dependency are both in place.

---

## 📄 License & Usage

No license file is currently present in the repository. Treat the code
as proprietary unless/until a license is added.
