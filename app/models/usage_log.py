# app/models/usage_log.py
from app import db
from datetime import datetime

class UsageLog(db.Model):
    __tablename__ = 'usage_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)  # 'analysis', 'improvement'
    model_used = db.Column(db.String(100), nullable=True)
    tokens_used = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(50), nullable=False, default='success')
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @classmethod
    def log(cls, user_id, action, model=None, tokens=0, status='success', ip=None):
        entry = cls(
            user_id=user_id,
            action=action,
            model_used=model,
            tokens_used=tokens,
            status=status,
            ip_address=ip,
        )
        db.session.add(entry)
        db.session.commit()
        return entry

    def to_dict(self):
        return {
            'id': self.id,
            'action': self.action,
            'model_used': self.model_used,
            'tokens_used': self.tokens_used,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<UsageLog user={self.user_id} action={self.action}>'
