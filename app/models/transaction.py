# app/models/transaction.py
from app import db
from datetime import datetime

class Transaction(db.Model):
    __tablename__ = 'transaction'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default='usd')
    type = db.Column(db.String(50), nullable=False)  # charge, refund
    status = db.Column(db.String(50), nullable=False, default='pending')
    provider = db.Column(db.String(50), nullable=False)
    external_id = db.Column(db.String(255), nullable=True)
    extra_data = db.Column(db.JSON, nullable=True)   # переименовано с metadata
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'amount': self.amount,
            'currency': self.currency,
            'type': self.type,
            'status': self.status,
            'provider': self.provider,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<Transaction {self.type} {self.amount} {self.status}>'
