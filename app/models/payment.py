# app/models/payment.py
from app import db
from datetime import datetime

class Payment(db.Model):
    __tablename__ = 'payment'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default='usd')
    status = db.Column(db.String(50), nullable=False, default='pending')
    provider = db.Column(db.String(50), nullable=False)  # stripe, paypal
    external_id = db.Column(db.String(255), nullable=True)
    plan_name = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'amount': self.amount,
            'currency': self.currency,
            'status': self.status,
            'provider': self.provider,
            'plan_name': self.plan_name,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<Payment {self.provider} {self.amount} {self.status}>'
