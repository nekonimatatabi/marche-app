from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    RadioField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Regexp

from marche.models import Role

# email-validator パッケージを足さずに済むよう、形だけを正規表現で確かめる
EMAIL = Regexp(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="メールアドレスの形式で入力してください"
)


class RegisterForm(FlaskForm):
    name = StringField("名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), EMAIL, Length(max=255)]
    )
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    # 選択肢に admin を入れない。送られてきても RadioField の検証で弾かれる
    role = RadioField(
        "登録の種類",
        choices=[(Role.CUSTOMER, "購入者"), (Role.PRODUCER, "生産者")],
        default=Role.CUSTOMER,
    )


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), EMAIL])
    password = PasswordField("パスワード", validators=[DataRequired()])


class JaIntegerField(IntegerField):
    """整数に変換できないときのエラーを日本語にした IntegerField（message= では変えられないため）"""

    def process_formdata(self, valuelist):
        if not valuelist or valuelist[0] == "":
            return
        try:
            self.data = int(valuelist[0])
        except ValueError:
            self.data = None
            raise ValueError("整数で入力してください") from None


class ProductForm(FlaskForm):
    name = StringField(
        "商品名",
        validators=[
            DataRequired(message="商品名を入力してください"),
            Length(max=50, message="50文字以内で入力してください"),
        ],
    )
    # DataRequired は 0 を「未入力」と扱う。在庫数に 0 を入れられるように InputRequired を使う
    price = JaIntegerField(
        "価格（円、税込み）",
        validators=[
            InputRequired(message="価格を入力してください"),
            NumberRange(min=50, max=100_000, message="50〜100,000円で入力してください"),
        ],
    )
    stock = JaIntegerField(
        "在庫数",
        validators=[
            InputRequired(message="在庫数を入力してください"),
            NumberRange(min=0, max=999, message="0〜999で入力してください"),
        ],
    )
    description = TextAreaField(
        "紹介文",
        validators=[Length(max=2000, message="2,000文字以内で入力してください")],
    )
    # 拡張子と大きさはここで、中身（先頭のバイト）はビューで uploads.detect_media_type で確かめる
    image = FileField(
        "写真（JPEG・PNG・WebP、5MB まで）",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                message="JPEG・PNG・WebP の画像を選んでください",
            ),
            FileSize(max_size=5 * 1024 * 1024, message="写真は 5MB までにしてください"),
        ],
    )
    is_published = BooleanField("公開する")
