from flask import Blueprint, request, jsonify
import bcrypt
from . import db  
from sqlalchemy import CheckConstraint

login_bp = Blueprint('login_bp', __name__, url_prefix='/lgn')

class User(db.Model):
    __tablename__ = 'auth_table'
    firstname = db.Column(db.String(50), nullable=False)
    lastname = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False, primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    salt = db.Column(db.String(255), nullable=False)  

    __table_args__ = (
        CheckConstraint(r"email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'", name='valid_email'),
    )

@login_bp.route('/get-salt/<email>', methods=['GET'])
def get_salt(email):
    try:
        user = User.query.filter_by(email=email).first()
        if user:
            return jsonify({"salt": user.salt}), 200
        return jsonify({"message": "User not found"}), 404
    except Exception as e:
        print(f"Salt retrieval error: {str(e)}")
        return jsonify({"message": "An error occurred while retrieving salt."}), 500

@login_bp.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')  

    user = User.query.filter_by(email=email).first()

    if not user:
        return jsonify({"message": "Invalid email or password!"}), 401

    try:
        if bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            return jsonify({
                "message": "Login successful!", 
                "firstname": user.firstname
            }), 200
        else:
            return jsonify({"message": "Invalid email or password!"}), 401
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({"message": "An error occurred during login."}), 500

@login_bp.route('/signup', methods=['POST'])
def signup():
    data = request.json
    firstname = data.get('firstname')
    lastname = data.get('lastname')
    email = data.get('email')
    password = data.get('password')  
    salt = data.get('salt')  

    if User.query.filter_by(email=email).first():
        return jsonify({"message": "User already exists!"}), 400

    try:
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        new_user = User(
            firstname=firstname,
            lastname=lastname,
            email=email,
            password_hash=password_hash,
            salt=salt  
        )
        
        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            "message": "User registered successfully!",
            "firstname": firstname
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"Signup error: {str(e)}")
        return jsonify({"message": "An error occurred during registration."}), 500