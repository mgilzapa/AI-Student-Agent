"""Create (or reuse) a throwaway smoke-test user and print an access token."""
import os, sys, json
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

URL = os.environ["SUPABASE_URL"]
SERVICE = os.environ["SUPABASE_SERVICE_KEY"]
ANON = os.environ["SUPABASE_ANON_KEY"]

EMAIL = "smoke-test-agent@example.com"
PW = "Smoke-Test-9482!xyz"

admin = create_client(URL, SERVICE)

if len(sys.argv) > 1 and sys.argv[1] == "delete":
    users = admin.auth.admin.list_users()
    for u in users:
        if u.email == EMAIL:
            admin.auth.admin.delete_user(u.id)
            print(f"deleted {u.id}")
    sys.exit(0)

# create if missing
try:
    admin.auth.admin.create_user({"email": EMAIL, "password": PW, "email_confirm": True})
except Exception as e:
    if "already" not in str(e).lower():
        raise

anon = create_client(URL, ANON)
sess = anon.auth.sign_in_with_password({"email": EMAIL, "password": PW})
print(json.dumps({"token": sess.session.access_token, "user_id": sess.user.id}))
