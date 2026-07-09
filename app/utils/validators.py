# app/utils/validators.py
import re

def validate_email(email):
    """Проверить корректность email."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_password(password):
    """Проверить надёжность пароля (минимум 8 символов)."""
    if not password or len(password) < 8:
        return False, 'Password must be at least 8 characters'
    return True, None

def validate_required_fields(data, fields):
    """Проверить наличие обязательных полей."""
    missing = [f for f in fields if not data.get(f)]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"
    return True, None
