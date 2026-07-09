# app/models/api_key.py
from app import db
from datetime import datetime
import hashlib
import secrets

class APIKey(db.Model):
    __tablename__ = 'api_keys'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    provider = db.Column(db.String(50), nullable=False, default='openrouter')
    name = db.Column(db.String(255), nullable=False)
    key_hash = db.Column(db.String(255), nullable=False, unique=True)
    key_prefix = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    total_tokens_used = db.Column(db.Integer, nullable=False, default=0)

    usage_logs = db.relationship('APIKeyUsageLog', backref='api_key', lazy='dynamic', cascade='all, delete-orphan')

    @staticmethod
    def hash_key(key):
        return hashlib.sha256(key.encode()).hexdigest()

    def can_be_used(self):
        if not self.is_active:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def mark_used(self, tokens=0):
        self.last_used_at = datetime.utcnow()
        self.usage_count += 1
        self.total_tokens_used += tokens
        db.session.commit()

    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'name': self.name,
            'key_prefix': self.key_prefix,
            'is_active': self.is_active,
            'is_primary': self.is_primary,
            'usage_count': self.usage_count,
            'created_at': self.created_at.isoformat(),
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }

    def __repr__(self):
        return f'<APIKey {self.provider} {self.name}>'


class APIKeyUsageLog(db.Model):
    __tablename__ = 'api_key_usage_logs'

    id = db.Column(db.Integer, primary_key=True)
    api_key_id = db.Column(db.Integer, db.ForeignKey('api_keys.id', ondelete='CASCADE'), nullable=False)
    request_id = db.Column(db.String(100), nullable=True)
    model_used = db.Column(db.String(100), nullable=True)
    tokens_used = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(50), nullable=False, default='success')
    error_message = db.Column(db.Text, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<APIKeyUsageLog key={self.api_key_id} status={self.status}>'
