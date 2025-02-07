from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

load_dotenv()

db = SQLAlchemy()
mail = Mail()

DB_USER = os.getenv('DB_USER')
DB_PWD = os.getenv('DB_PWD')
DB_ENDP = os.getenv('DB_ENDP')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')
MAIL_SERVER = os.getenv('MAIL_SERVER')
MAIL_PORT = os.getenv('MAIL_PORT')
MAIL_USE_TLS = True
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER')

def create_database_if_not_exists():
    try:
        conn = psycopg2.connect(
            user=DB_USER,
            password=DB_PWD,
            host=DB_ENDP,
            port=DB_PORT,
            database='postgres'
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        cursor.execute(f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{DB_NAME}'")
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute(f'CREATE DATABASE {DB_NAME}')
            print(f"Database '{DB_NAME}' created successfully!")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"Error while creating database: {e}")
        raise e

def create_app():
    app = Flask(__name__, static_folder='public')
    
    create_database_if_not_exists()
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DB_USER}:{DB_PWD}@{DB_ENDP}:{DB_PORT}/{DB_NAME}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    app.config['MAIL_SERVER'] = MAIL_SERVER
    app.config['MAIL_PORT'] = int(MAIL_PORT)
    app.config['MAIL_USE_TLS'] = MAIL_USE_TLS
    app.config['MAIL_USERNAME'] = MAIL_USERNAME
    app.config['MAIL_PASSWORD'] = MAIL_PASSWORD
    app.config['MAIL_DEFAULT_SENDER'] = MAIL_DEFAULT_SENDER
    
    CORS(app, resources={r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"]
    }})
    
    db.init_app(app)
    mail.init_app(app)

    from .inventory import inventory_bp
    from .platform_dash import platform_bp
    from .custsol_dash import custsol_bp
    from .login import login_bp

    app.register_blueprint(inventory_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(custsol_bp)
    app.register_blueprint(login_bp)

    with app.app_context():
        db.create_all()
        print("Database tables created successfully!")

    return app

app = create_app()