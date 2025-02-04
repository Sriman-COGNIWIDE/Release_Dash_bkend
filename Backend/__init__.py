from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

load_dotenv()

db = SQLAlchemy()

DB_USER = os.getenv('DB_USER')
DB_PWD = os.getenv('DB_PWD')
DB_ENDP = os.getenv('DB_ENDP')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

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
    
    CORS(app, resources={r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"]
    }})
    
    db.init_app(app)

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