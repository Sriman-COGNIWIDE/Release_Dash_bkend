from flask import Blueprint, request, jsonify
import bcrypt
from . import db, mail
from sqlalchemy import CheckConstraint
import random
from flask_mail import Message

login_bp = Blueprint('login_bp', __name__, url_prefix='/lgn')

class User(db.Model):
    __tablename__ = 'auth_table_2'
    firstname = db.Column(db.String(50), nullable=False)
    lastname = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False, primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    otp = db.Column(db.String(6), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    salt = db.Column(db.String(255), nullable=False)  

    __table_args__ = (
        CheckConstraint(r"email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'", name='valid_email'),
    )

def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(recipient_email, firstname, otp):
    subject = "Your OTP for Account Verification"
    with open("email_template.html", "r", encoding="utf-8") as file:
        html_content = file.read()

    # Replace placeholders in the HTML file with actual values
    html_content = html_content.replace("{{firstname}}", firstname)
    html_content = html_content.replace("{{otp}}", str(otp))
    try:
        msg = Message(
            subject,
            recipients=[recipient_email],
            html=html_content,
            sender=mail.default_sender
        )
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

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
        otp = generate_otp()

        new_user = User(
            firstname=firstname,
            lastname=lastname,
            email=email,
            password_hash=password_hash,
            salt=salt,
            otp=otp
        )

        db.session.add(new_user)
        db.session.commit()
        
        if send_otp_email(email, firstname, otp):
            return jsonify({"message": "User registered successfully! OTP sent to email."}), 201
        else:
            return jsonify({"message": "User registered successfully, but OTP email failed to send."}), 201

    except Exception as e:
        db.session.rollback()
        print(f"Signup error: {str(e)}")
        return jsonify({"message": "An error occurred during registration."}), 500
    
@login_bp.route('/verify-email-otp', methods=['POST'])
def verify_email_otp():
    print("Verify Email OTP route hit!")  
    data = request.json
    email = data.get('email')
    entered_otp = data.get('otp')

    user = User.query.filter_by(email=email).first()
    if not user:
        print("User not found!")  
        return jsonify({"message": "User not found!"}), 404

    if not user.otp:
        print("OTP expired or already verified!")  
        return jsonify({"message": "OTP expired or already verified!"}), 400

    if user.otp == entered_otp:
        user.is_verified = True
        user.otp = None  
        db.session.commit()
        print("Email verified successfully!") 
        return jsonify({"message": "Email verified successfully! Your account is now active."}), 200
    else:
        print("Invalid OTP!")  
        return jsonify({"message": "Invalid OTP!"}), 400