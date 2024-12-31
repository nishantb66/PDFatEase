import os
from flask import Flask
from app.routes import main  # Import the main blueprint


def create_app():
    app = Flask(__name__)

    # Set the upload folder
    app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "app", "uploads")

    # Ensure the upload folder exists
    if not os.path.exists(app.config["UPLOAD_FOLDER"]):
        os.makedirs(app.config["UPLOAD_FOLDER"])

    # Secret key for session management, should ideally be loaded from environment variables
    app.secret_key = os.environ.get(
        "SECRET_KEY",
        "b\x95\xc4\xd4\xef#\x82\x9aW\xb9\x92\xf1gD\xf8\x8a*\xb6\xa5\xa2r\xd3\xd1P",
    )

    # Register the blueprint
    app.register_blueprint(main)

    return app
