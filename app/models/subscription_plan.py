# app/models/subscription_plan.py
from app import db
from datetime import datetime

class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plan'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    price_usd = db.Column(db.Float, nullable=False, default=0.0)
    analysis_quota = db.Column(db.Integer, nullable=False, default=2)  # -1 = безлимит
    improvement_quota = db.Column(db.Integer, nullable=False, default=0)
    custom_api_key = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    stripe_price_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'plan_name': self.name,
            'display_name': self.display_name,
            'price_usd': self.price_usd,
            'analysis_quota': self.analysis_quota,
            'improvement_quota': self.improvement_quota,
            'custom_api_key': self.custom_api_key,
            'features': self._get_features(),
        }

    def _get_features(self):
        features = []
        if self.analysis_quota == -1:
            features.append('Unlimited analyses')
        else:
            features.append(f'{self.analysis_quota} analyses/month')
        if self.improvement_quota == -1:
            features.append('Unlimited improvements')
        elif self.improvement_quota > 0:
            features.append(f'{self.improvement_quota} improvements/month')
        if self.custom_api_key:
            features.append('Custom API key support')
        return features

    def __repr__(self):
        return f'<SubscriptionPlan {self.name}>'
