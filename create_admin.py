import getpass
import sys
from datetime import datetime, timedelta
from app import create_app, db
from app.models import User, Subscription

# Пароль запрашивается при запуске и нигде не хранится.
password = getpass.getpass('Admin password (min 10 characters): ')
if len(password) < 10 or password != getpass.getpass('Repeat password: '):
    print('Password too short or does not match. Nothing was changed.')
    sys.exit(1)

app = create_app()

with app.app_context():
    email = 'slvm972@gmail.com'
    u = User.query.filter_by(email=email).first()
    if not u:
        u = User(email=email)
        db.session.add(u)
        db.session.commit()

    u.set_password(password)
    if hasattr(u, 'is_admin'):
        u.is_admin = True
    if hasattr(u, 'role'):
        u.role = 'admin'

    # Находим или создаем активную подписку с балансом
    sub = Subscription.query.filter_by(user_id=u.id).first()
    if not sub:
        sub = Subscription(user_id=u.id)
        db.session.add(sub)

    sub.status = 'active'
    sub.plan_name = 'pro'
    sub.improvement_credits = 9999
    sub.credits_granted = 9999
    sub.improvement_used = 0
    sub.analysis_used = 0
    sub.credits_used = 0
    sub.current_period_start = datetime.utcnow()
    sub.current_period_end = datetime.utcnow() + timedelta(days=365)

    db.session.commit()
    print('Admin subscription and credits successfully updated!')