# app/services/auth_service.py
from app import db
from app.models.user import User
from app.models.subscription import Subscription
from app.services.jwt_service import JWTService
from app.utils.validators import validate_email, validate_password
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# bcrypt учитывает только первые 72 байта; более длинный пароль не даёт защиты, а нагрузку создаёт.
MAX_PASSWORD_LENGTH = 128

class AuthService:

    @staticmethod
    def register(email, password):
        """Зарегистрировать нового пользователя."""
        # Проверки входа — в одном месте, чтобы защита действовала для ВСЕХ маршрутов
        # (/api/user/register, /api/auth/register, /auth/register) и до обращения к БД.
        email = email.strip().lower() if isinstance(email, str) else ''
        if not validate_email(email):
            return {'success': False, 'error': 'Invalid email format'}

        # Пароль НЕ обрезаем и регистр не меняем: вход сверяет ровно то, что ввёл пользователь.
        valid, msg = validate_password(password if isinstance(password, str) else '')
        if not valid:
            return {'success': False, 'error': msg}
        if len(password) > MAX_PASSWORD_LENGTH:
            return {'success': False,
                    'error': 'Password must be at most %d characters' % MAX_PASSWORD_LENGTH}

        if User.query.filter_by(email=email.lower()).first():
            return {'success': False, 'error': 'Email already registered'}

        try:
            user = User(email=email.lower())
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            # Создать бесплатную подписку
            # credits_granted/credits_used заданы явно — не полагаемся на
            # column-default в модели для бизнес-логики выдачи стартовых
            # кредитов: default=1 в Subscription сейчас даёт другой результат,
            # но если его когда-нибудь изменят по другой причине (например,
            # для другого места создания Subscription), регистрация не должна
            # молча изменить поведение.
            # Бизнес-правило: free-план даёт 2 кредита на единый пул
            # (два бесплатных действия — analyze и/или improve, суммарно).
            subscription = Subscription(
                user_id=user.id,
                plan_name='free',
                status='active',
                credits_granted=2,
                credits_used=0,
            )
            db.session.add(subscription)

            # Оплаченные, но не привязанные к аккаунту платежи Lemon Squeezy
            # (вебхук пришёл раньше регистрации, либо email заказа не совпал
            # с аккаунтом на момент оплаты): зачисляем в ЭТОЙ ЖЕ транзакции,
            # до commit. Любая ошибка ниже откатывает всю регистрацию.
            # Сравнение email — в нижнем регистре, без пробелов по краям.
            from app.models.payment import Payment
            from app.utils.plans import credits_for_plan
            email_norm = (email or '').strip().lower()
            pending = Payment.query.filter(
                Payment.provider == 'lemonsqueezy',
                Payment.user_id.is_(None),
                Payment.status.in_(('completed', 'completed_unclaimed')),
                db.func.lower(db.func.trim(Payment.payer_email)) == email_norm,
            ).all()
            claimed_ids = []
            for payment in pending:
                # Число кредитов определяет купленный пакет (plan_name NULL = старый
                # платёж = 10; неизвестное имя = 0, но платёж всё равно привязывается).
                credits = credits_for_plan(payment.plan_name)
                subscription.credits_granted += credits
                subscription.improvement_credits = (subscription.improvement_credits or 0) + credits // 2
                payment.user_id = user.id
                payment.status = 'completed'
                payment.description = (payment.description or '') + ' (claimed at registration)'
                claimed_ids.append(payment.id)

            db.session.commit()

            if claimed_ids:
                # В лог — только число и id платежей, без email.
                logger.info(
                    "Registration claimed %d pending payment(s): ids=%s",
                    len(claimed_ids), claimed_ids,
                )

            logger.info(f"New user registered: {email}")
            return {'success': True, 'user': user}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Registration error: {str(e)}")
            return {'success': False, 'error': 'Registration failed'}

    @staticmethod
    def login(email, password):
        """Войти в систему."""
        user = User.query.filter_by(email=email.lower()).first()

        if not user or not user.check_password(password):
            return {'success': False, 'error': 'Invalid email or password'}

        if not user.is_active:
            return {'success': False, 'error': 'Account is disabled'}

        user.last_login_at = datetime.utcnow()
        db.session.commit()

        access_token = JWTService.create_access_token(user.id)
        refresh_token = JWTService.create_refresh_token(user.id)

        return {
            'success': True,
            'user': user,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }

    @staticmethod
    def refresh_token(user_id):
        """Обновить access token."""
        user = User.query.get(user_id)
        if not user or not user.is_active:
            return {'success': False, 'error': 'User not found'}

        access_token = JWTService.create_access_token(user.id)
        return {'success': True, 'access_token': access_token}
