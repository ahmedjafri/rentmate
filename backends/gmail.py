# backends/gmail.py
"""Gmail backend for polling inbound tenant emails and sending replies.

Required environment variables:
  GMAIL_CLIENT_ID      — OAuth2 client ID
  GMAIL_CLIENT_SECRET  — OAuth2 client secret
  GMAIL_REFRESH_TOKEN  — refresh token from one-time OAuth2 installed-app flow
  GMAIL_SENDER_ADDRESS — e.g. rentmate@yourdomain.com
"""

import base64
import email as _email_lib
import os
from email.mime.text import MIMEText
from typing import Optional


def _build_service():
    """Build an authenticated Gmail API service object."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


def _header(headers: list, name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


class GmailClient:
    """Thin wrapper around the Gmail API for RentMate's email channel."""

    def poll_unread(self) -> list[dict]:
        """
        Fetch all unread messages in the inbox, mark them read, and return
        a list of dicts with keys:
          {from_address, subject, body_plain, thread_id, message_id, date}
        """
        service = _build_service()
        results = service.users().messages().list(
            userId="me",
            q="is:unread in:inbox",
            maxResults=50,
        ).execute()

        messages = results.get("messages", [])
        output = []

        for ref in messages:
            msg_id = ref["id"]
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = msg.get("payload", {}).get("headers", [])
            body = _decode_body(msg.get("payload", {}))

            output.append({
                "from_address": _header(headers, "From"),
                "subject": _header(headers, "Subject"),
                "body_plain": body,
                "thread_id": msg.get("threadId"),
                "message_id": msg_id,
                "date": _header(headers, "Date"),
            })

            # Mark as read
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()

        return output

    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
    ):
        """
        Send an email reply. If thread_id is provided the message is threaded.
        Subject is auto-prefixed with "Re: " when a thread_id is present and
        the subject doesn't already start with "Re:".
        """
        service = _build_service()
        sender = os.environ.get("GMAIL_SENDER_ADDRESS", "me")

        if thread_id and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        mime_msg = MIMEText(body)
        mime_msg["to"] = to
        mime_msg["from"] = sender
        mime_msg["subject"] = subject

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        send_body: dict = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        service.users().messages().send(
            userId="me", body=send_body
        ).execute()
