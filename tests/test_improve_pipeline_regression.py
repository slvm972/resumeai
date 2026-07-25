"""
Regression-тест на найденный баг: в _run_improve_pipeline() (первый,
не-retry вызов к Groq) payload ссылался на user_prompt, которая нигде не
была объявлена -> гарантированный NameError при любом реальном вызове.

Затронутое место: app/missing_routes4.py -> _run_improve_pipeline()

Запуск: python -m pytest tests/test_improve_pipeline_regression.py -v
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def _fake_groq_response(*args, **kwargs):
    """
    Минимальный правдоподобный ответ Groq. Оба элемента тестового резюме
    (i<=1) попадают под hard-freeze правило в _run_improve_pipeline,
    поэтому реальное содержимое ответа не важно для freeze-веток —
    важно только что вызов вообще происходит без NameError и что payload
    дошёл до requests.post с непустым user-сообщением.
    """
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        'choices': [{'message': {'content': '###ITEM_001###\nok\n\n###ITEM_002###\nok'}}],
        'usage': {'total_tokens': 10},
    }
    return resp


def test_run_improve_pipeline_no_nameerror_on_first_call(app_ctx):
    """
    Прежде функция падала с NameError: name 'user_prompt' is not defined
    на самом первом (не-retry) вызове — т.е. буквально при любом реальном
    использовании /api/improve. Мокаем requests.post и проверяем:
    (1) функция не роняет NameError, (2) доходит до сборки payload,
    (3) messages[1] (user-сообщение) непустое и содержит блоки резюме.
    """
    from app.missing_routes4 import _run_improve_pipeline

    resume_text = "John Doe\njohn.doe@example.com"  # >=20 символов, 2 строки -> обе hard-freeze (i<=1)

    with patch('requests.post', side_effect=_fake_groq_response) as mock_post:
        result = _run_improve_pipeline(
            original_bytes=None,
            filename=None,
            resume_text_fallback=resume_text,
            api_key='dummy-key',
        )

    assert result['success'] is True

    assert mock_post.called
    sent_payload = mock_post.call_args.kwargs.get('json')
    assert sent_payload is not None

    messages = sent_payload['messages']
    assert messages[0]['role'] == 'system'
    assert messages[1]['role'] == 'user'
    user_content = messages[1]['content']
    assert isinstance(user_content, str)
    assert user_content.strip() != ''
    # user_prompt должен реально содержать защищённые блоки резюме, а не
    # быть случайно пустой строкой/None, дошедшей до payload.
    assert '###ITEM_' in user_content
