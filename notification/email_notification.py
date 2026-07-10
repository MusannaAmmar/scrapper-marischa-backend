import os
import json
import requests
from msal import PublicClientApplication,SerializableTokenCache
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")  # Optional if using device code / public client flow
TENANT = "common"  # or "consumers" for only personal accounts
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"
SCOPES = ["https://graph.microsoft.com/Mail.Send", "https://graph.microsoft.com/User.Read"]

SENDER_EMAIL = os.getenv("SENDER_EMAIL")

CACHE_FILE = "msal_token_cache.json"  # This file will be created in the same folder as your script

def get_access_token():
    """
    Gets OAuth access token using device code flow (interactive only first time).
    Persists cache to file for silent logins on future runs.
    """
    # Create SerializableTokenCache
    cache = SerializableTokenCache()

    # Load existing cache from file if it exists
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache.deserialize(f.read())
            print("Loaded cached tokens from file")
        except Exception as e:
            print(f"Cache load failed (corrupted?): {e}. Starting fresh.")
            cache = SerializableTokenCache()  # Reset if bad

    app = PublicClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,  # <-- Attach the persistent cache here
    )

    # Try silent acquisition first
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result:
            print("Using cached token (silent login)")

    # If no valid token, do device code flow (interactive)
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise Exception(f"Failed to create device flow: {json.dumps(flow, indent=2)}")

        print("\n=== One-Time Login Required ===")
        print(flow["message"])
        print("=============================\n")

        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise Exception(f"Authentication failed: {result.get('error_description')}")

        print("Login successful! Token acquired.")
        # Save the updated cache to file immediately
        with open(CACHE_FILE, "w") as f:
            f.write(cache.serialize())
        print(f"Saved tokens to {CACHE_FILE} for future automatic use")

    return result["access_token"]


    
def send_notification_email(recipient_email: str, subject: str = "Notification", message: str = "Generic notification message"):
    """
    Send email using Microsoft Graph API with OAuth.
    """
    try:
        access_token = get_access_token()

        endpoint = "https://graph.microsoft.com/v1.0/me/sendMail"

        email_body = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": message
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": recipient_email
                        }
                    }
                ]
            },
            "saveToSentItems": "true"
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        response = requests.post(endpoint, headers=headers, json=email_body)

        if response.status_code in [200, 202, 204]:
            print(f"Email sent successfully to {recipient_email}")
            return True
        else:
            print(f"Graph error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

# Test
if __name__ == "__main__":
    send_notification_email(
        recipient_email=os.getenv("RECIPIENT_EMAIL"),
        subject="Test Notification",
        message="This is a test from your AI Scraping Agent."
    )