import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from main import SessionLocal, UserAccount, encrypt_password, SystemSetting

def create_admin():
    db = SessionLocal()
    try:
        user = db.query(UserAccount).filter(UserAccount.username == "admin").first()
        if not user:
            print("Creating default admin account...")
            
            # Get default password from settings or use "1234"
            setting = db.query(SystemSetting).first()
            pw = setting.admin_password if setting and setting.admin_password else "1234"
            
            enc_pw = encrypt_password(pw)
            new_user = UserAccount(
                username="admin",
                password=enc_pw,
                full_name="System Administrator",
                role="admin",
                status="approved"
            )
            db.add(new_user)
            db.commit()
            print(f"Admin account created successfully. User: admin, Password: {pw}")
        else:
            print("Admin account already exists.")
    except Exception as e:
        print(f"Error creating admin: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_admin()

