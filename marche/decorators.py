from functools import wraps

from flask import abort
from flask_login import current_user

from marche.extensions import login_manager


def role_required(*roles):
    """ログインしていなければログイン画面へ（login_required と同じ動き）、ロールが違えば 403"""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
