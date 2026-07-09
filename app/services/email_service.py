# app/services/email_service.py
from flask import current_app
from flask_mail import Message
from app import mail
import logging

logger = logging.getLogger(__name__)

class EmailService:

    @staticmethod
    def send_welcome_email(user):
        """Отправить приветственное письмо."""
        try:
            msg = Message(
                subject='Welcome to ResumeAI!',
                recipients=[user.email],
                html=f"""
                <h1>Welcome to ResumeAI!</h1>
                <p>Hi {user.email},</p>
                <p>Your account has been created successfully.</p>
                <p>You are on the <strong>Free plan</strong> with 2 analyses per month.</p>
                <p>Start analyzing your resume today!</p>
                """,
            )
            mail.send(msg)
            logger.info(f"Welcome email sent to {user.email}")
            return True
        except Exception as e:
            logger.error(f"Error sending welcome email: {str(e)}")
            return False

    @staticmethod
    def send_password_reset_email(user, reset_token):
        """Отправить письмо для сброса пароля."""
        try:
            reset_url = f"{current_app.config.get('FRONTEND_URL')}/reset-password?token={reset_token}"
            msg = Message(
                subject='Reset your ResumeAI password',
                recipients=[user.email],
                html=f"""
                <h1>Password Reset</h1>
                <p>Click the link below to reset your password:</p>
                <p><a href="{reset_url}">Reset Password</a></p>
                <p>This link expires in 1 hour.</p>
                <p>If you did not request this, ignore this email.</p>
                """,
            )
            mail.send(msg)
            return True
        except Exception as e:
            logger.error(f"Error sending reset email: {str(e)}")
            return False

    @staticmethod
    def send_subscription_confirmation(user, plan_name):
        """Отправить подтверждение подписки."""
        try:
            msg = Message(
                subject=f'ResumeAI - {plan_name.title()} Plan Activated',
                recipients=[user.email],
                html=f"""
                <h1>Subscription Activated!</h1>
                <p>Hi {user.email},</p>
                <p>Your <strong>{plan_name.title()}</strong> plan is now active.</p>
                <p>Thank you for subscribing to ResumeAI!</p>
                """,
            )
            mail.send(msg)
            return True
        except Exception as e:
            logger.error(f"Error sending subscription email: {str(e)}")
            return False
