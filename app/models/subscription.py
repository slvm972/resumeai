# app/models/subscription.py
from app import db
from datetime import datetime

class Subscription(db.Model):
    __tablename__ = 'subscription'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name = db.Column(db.String(50), nullable=False, default='free')
    status = db.Column(db.String(50), nullable=False, default='active')
    payment_provider = db.Column(db.String(50), nullable=True)  # stripe, paypal
    external_subscription_id = db.Column(db.String(255), nullable=True)
    current_period_start = db.Column(db.DateTime, nullable=True)
    current_period_end = db.Column(db.DateTime, nullable=True)
    next_billing_date = db.Column(db.DateTime, nullable=True)
    analysis_used = db.Column(db.Integer, nullable=False, default=0)
    improvement_used = db.Column(db.Integer, nullable=False, default=0)
    # Накопительный остаток кредитов на Improve. Пополняется на +5 при каждой
    # покупке пакета (one-time, без подписки) — покупки складываются (stacking),
    # а не сбрасывают счётчик. Остаток = improvement_credits - improvement_used.
    improvement_credits = db.Column(db.Integer, nullable=False, default=0)
    # Единый пул кредитов — тратится и на analyze, и на improve.
    # credits_granted: сколько всего выдано (2 при регистрации + 10 за каждую покупку, стакается).
    # credits_used: сколько всего потрачено (растёт при каждом analyze и improve).
    # Старые поля (analysis_used, improvement_used, improvement_credits) оставлены
    # нетронутыми на этом шаге — переключение логики на новый пул будет отдельным шагом.
    credits_granted = db.Column(db.Integer, nullable=False, default=2)
    credits_used = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def improvement_remaining(self):
        """Сколько Improve-кредитов ещё доступно для использования."""
        return max(self.improvement_credits - self.improvement_used, 0)

    def credits_remaining(self):
        """Сколько кредитов единого пула ещё доступно (analyze + improve вместе)."""
        return max(self.credits_granted - self.credits_used, 0)

    def to_dict(self):
        return {
            'id': self.id,
            'plan_name': self.plan_name,
            'status': self.status,
            'payment_provider': self.payment_provider,
            'analysis_used': self.analysis_used,
            'improvement_used': self.improvement_used,
            'improvement_credits': self.improvement_credits,
            'improvement_remaining': self.improvement_remaining(),
            'credits_granted': self.credits_granted,
            'credits_used': self.credits_used,
            'credits_remaining': self.credits_remaining(),
            'next_billing_date': self.next_billing_date.isoformat() if self.next_billing_date else None,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<Subscription user={self.user_id} plan={self.plan_name}>'
