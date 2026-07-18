# app/routes/webhooks.py
import hmac
import hashlib
import json
import logging

from flask import Blueprint, request, current_app

from app import db
from app.models.user import User
from app.models.payment import Payment

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint('webhooks', __name__)


@webhooks_bp.route('/lemonsqueezy', methods=['POST'])
def lemonsqueezy_webhook():
    # --- 1. Сырое тело — подпись считается от байт, не от JSON ---
    raw_body = request.get_data()

    # --- 2. Проверка подписи ДО любого парсинга ---
    secret = current_app.config.get('LEMONSQUEEZY_WEBHOOK_SECRET')
    signature = request.headers.get('X-Signature')

    if not secret or not signature:
        return '', 401

    expected_signature = hmac.new(
        secret.encode('utf-8'), raw_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return '', 401

    # --- 3. Теперь можно парсить JSON ---
    try:
        event = json.loads(raw_body)
    except (ValueError, TypeError):
        return '', 400

    # --- 4. Интересует только оплаченный order_created ---
    event_name = request.headers.get('X-Event-Name')
    attributes = event.get('data', {}).get('attributes', {})

    if event_name != 'order_created' or attributes.get('status') != 'paid':
        # Не наше событие — 200, чтобы Lemon Squeezy не ретраил
        return '', 200

    # --- 5. Извлечение полей ---
    # ДОПУЩЕНИЕ, ТРЕБУЮЩЕЕ ПРОВЕРКИ: по документации Lemon Squeezy поле
    # `total` приходит в минорных единицах (центах), поэтому делим на 100.
    # Свериться на первом реальном тестовом вебхуке после деплоя и поправить
    # здесь, если формат окажется другим.
    external_id = attributes.get('identifier')
    user_email = (attributes.get('user_email') or '').strip().lower()
    total_cents = attributes.get('total', 0)
    amount = round(total_cents / 100, 2)

    if not external_id or not user_email:
        logger.error(
            "Lemon Squeezy webhook: missing identifier or user_email in payload"
        )
        return '', 400

    try:
        # --- 6. Идемпотентность ---
        existing = Payment.query.filter_by(external_id=external_id).first()
        if existing:
            return '', 200

        # --- 7 / 8. Найти пользователя, создать Payment ---
        user = User.query.filter_by(email=user_email).first()

        if user:
            subscription = user.get_active_subscription()
            if subscription:
                subscription.improvement_credits += 5
            else:
                # Защитный случай — не должен происходить в норме
                # (подписка создаётся при регистрации), но не роняем вебхук.
                logger.error(
                    "Lemon Squeezy webhook: user %s has no active subscription, "
                    "credits not applied (order %s)", user_email, external_id
                )

            payment = Payment(
                user_id=user.id,
                payer_email=user_email,
                amount=amount,
                currency=attributes.get('currency', 'usd'),
                status='completed',
                provider='lemonsqueezy',
                external_id=external_id,
                description='Lemon Squeezy order',
            )
        else:
            logger.warning(
                "Lemon Squeezy payment for unknown user: %s, order %s",
                user_email, external_id
            )
            payment = Payment(
                user_id=None,
                payer_email=user_email,
                amount=amount,
                currency=attributes.get('currency', 'usd'),
                status='completed',
                provider='lemonsqueezy',
                external_id=external_id,
                description='Lemon Squeezy order (unmatched user)',
            )

        db.session.add(payment)
        db.session.commit()

    except Exception:
        # Ошибка ДО успешного commit — платёж точно не сохранён.
        # 500 → Lemon Squeezy повторит запрос; идемпотентность через
        # external_id защитит от задвоения при повторе.
        db.session.rollback()
        logger.exception(
            "Lemon Squeezy webhook failed before commit (order %s)", external_id
        )
        return '', 500

    return '', 200
