import click

from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User
from marche.uploads import delete_image

SEED_PASSWORD = "password"

# (email, 名前, ロール)
SEED_USERS = [
    ("admin@example.com", "管理者", Role.ADMIN),
    ("producer1@example.com", "高原ファーム", Role.PRODUCER),
    ("producer2@example.com", "岩手山麓工房", Role.PRODUCER),
    ("customer1@example.com", "購入者1", Role.CUSTOMER),
    ("customer2@example.com", "購入者2", Role.CUSTOMER),
]

# (生産者の email, 商品名, 価格, 在庫, 公開するか, 紹介文)
SEED_PRODUCTS = [
    (
        "producer1@example.com",
        "八幡平マッシュルーム 200g",
        450,
        20,
        True,
        "八幡平の湧き水で育てた、肉厚のマッシュルームです。\nソテーやスープにどうぞ。",
    ),
    (
        "producer1@example.com",
        "安代りんどう 切り花 5本",
        800,
        10,
        True,
        "安代の高原で育てたりんどうです。\n花もちがよく、2週間ほど楽しめます。",
    ),
    (
        "producer1@example.com",
        "ほうれん草 1束",
        180,
        30,
        True,
        "朝どれのほうれん草です。寒さで甘みが増しています。",
    ),
    (
        "producer1@example.com",
        "試作中の干し野菜セット",
        600,
        5,
        False,
        "にんじん、大根、しいたけの干し野菜です。試作中のため非公開です。",
    ),
    (
        "producer2@example.com",
        "八幡平ポーク ロース 300g",
        1200,
        8,
        True,
        "八幡平の麓で育てた豚のロースです。\n生姜焼きやとんかつに向いています。",
    ),
    (
        "producer2@example.com",
        "手づくり味噌 1kg",
        1300,
        12,
        True,
        "地元の大豆と米麹で仕込み、1年寝かせた味噌です。",
    ),
    (
        "producer2@example.com",
        "りんごジュース 1L",
        700,
        15,
        True,
        "岩手山麓のりんごを搾った、ストレート果汁100%のジュースです。",
    ),
    (
        "producer2@example.com",
        "地熱野菜のピクルス",
        550,
        0,
        True,
        "地熱ハウスで育てた野菜を、甘酢に漬けました。\n次の入荷までお待ちください。",
    ),
]


def init_app(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="メールアドレス")
    @click.option("--name", prompt="名前", default="管理者")
    @click.password_option("--password", prompt="パスワード")
    def create_admin(email, name, password):
        """管理者を1人作る（画面からは作れないため）"""
        email = email.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            raise click.ClickException("このメールアドレスは登録済みです")
        if len(password) < 8:
            raise click.ClickException("パスワードは8文字以上にしてください")
        user = User(name=name, email=email, role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"管理者 {email} を作りました")

    @app.cli.command("seed")
    def seed():
        """開発用の初期データを入れる。全てのデータを消してから入れ直す（本番では実行しない）"""
        image_filenames = db.session.scalars(
            db.select(Product.image_filename).where(Product.image_filename.is_not(None))
        ).all()
        # 外部キーで止められないように、参照している側（明細）から消す
        for model in (OrderItem, Order, Product, User):
            db.session.execute(db.delete(model))
        # 消した行のオブジェクトがセッションに残っていると、同じ id の新しい行と重なるので外す
        db.session.expunge_all()

        users = {}
        for email, name, role in SEED_USERS:
            user = User(name=name, email=email, role=role)
            user.set_password(SEED_PASSWORD)
            db.session.add(user)
            users[email] = user
        db.session.flush()  # users の id を決める

        for email, name, price, stock, is_published, description in SEED_PRODUCTS:
            db.session.add(
                Product(
                    producer_id=users[email].id,
                    name=name,
                    price=price,
                    stock=stock,
                    is_published=is_published,
                    description=description,
                )
            )
        db.session.commit()

        for filename in image_filenames:  # DB から消せてから写真を消す
            delete_image(filename)
        click.echo(
            f"初期データを入れました（ユーザー {len(SEED_USERS)}人、"
            f"商品 {len(SEED_PRODUCTS)}件）"
        )
