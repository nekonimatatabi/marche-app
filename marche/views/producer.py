from flask import Blueprint, render_template

from marche.decorators import role_required
from marche.models import Role

bp = Blueprint("producer", __name__, url_prefix="/producer")


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    # 出品管理（自分の商品の一覧）は、03 章で作り直す
    return render_template("producer/index.html")
