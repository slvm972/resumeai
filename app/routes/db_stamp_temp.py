# app/routes/db_stamp_temp.py
# TEMP-B1-DBSTAMP — временный файл, удалить целиком после успешного stamp на проде.
import os
import hmac
from flask import Blueprint, jsonify, request, current_app

db_stamp_bp = Blueprint('db_stamp_temp', __name__)

BASELINE_REV = '1defa6cc1306'
HEAD_REV = '2d3b2a4a8dd3'


@db_stamp_bp.route('/api/_internal/db-stamp-upgrade', methods=['POST'])
def db_stamp_upgrade():
    expected_token = os.environ.get('DB_STAMP_TOKEN')
    if not expected_token:
        return jsonify({'error': 'not found'}), 404

    provided_token = request.headers.get('X-DB-Stamp-Token', '')
    if not hmac.compare_digest(provided_token, expected_token):
        return jsonify({'error': 'not found'}), 404

    from alembic.config import Config
    from alembic import command
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    alembic_dir = os.path.join(root_dir, 'alembic')
    db_url = current_app.config['SQLALCHEMY_DATABASE_URI']

    cfg = Config()
    cfg.set_main_option('script_location', alembic_dir)
    cfg.set_main_option('sqlalchemy.url', db_url)

    engine = create_engine(db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        heads = ctx.get_current_heads()
    current_rev = heads[0] if heads else None

    result = {'before': current_rev}

    if current_rev == HEAD_REV:
        result['action'] = 'noop_already_at_head'
        return jsonify(result), 200

    if current_rev not in (None, BASELINE_REV):
        result['error'] = f'Unexpected current revision {current_rev!r} — aborting'
        return jsonify(result), 409

    try:
        if current_rev is None:
            command.stamp(cfg, BASELINE_REV)
            result['stamped'] = BASELINE_REV
        command.upgrade(cfg, 'head')
        result['upgraded_to'] = HEAD_REV
    except Exception as e:
        result['error'] = str(e)
        return jsonify(result), 500

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        after_heads = ctx.get_current_heads()
    result['after'] = after_heads[0] if after_heads else None

    return jsonify(result), 200