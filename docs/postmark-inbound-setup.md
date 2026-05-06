# Postmark Inbound Email Setup

This doc covers everything needed to wire up real inbound emails to RentMate. The webhook endpoint (`/api/email/inbound`) is already built — you just need to point Postmark at it.

---

## 1. Create a Postmark Account and Server

1. Go to [postmarkapp.com](https://postmarkapp.com) and sign up (free tier: 100 inbound/month)
2. Create a new **Server** — name it "RentMate Inbound" or similar
3. Inside that server, go to the **Inbound** tab

---

## 2. Set the Webhook URL

In the Inbound tab, set the **Inbound Webhook URL** to:

```
https://<your-public-rentmate-url>/api/email/inbound
```

For local testing without DNS, use [ngrok](https://ngrok.com):
```bash
ngrok http 8002
# Copy the https URL → paste it as the webhook URL in Postmark
```

---

## 3. Copy the Webhook Token

On the same Inbound settings page, Postmark shows a **Webhook Token**. Copy it.

Set it as an environment variable on your server:
```
POSTMARK_INBOUND_WEBHOOK_TOKEN=<token from Postmark>
```

Or save it in the RentMate settings UI under **Integrations → Email → Webhook Token**.

---

## 4. Add the MX DNS Record

In your DNS provider (Cloudflare, Route53, GoDaddy, etc.), add this record for `rentmate.io`:

| Type | Name | Value | Priority | TTL |
|------|------|-------|----------|-----|
| MX | `snoresidences` | `inbound.postmarkapp.com` | `10` | `300` |

This tells mail servers to deliver anything sent to `*@snoresidences.rentmate.io` to Postmark.

> **Note:** DNS propagation can take up to 48 hours, but usually under 5 minutes with Cloudflare.

---

## 5. Enable the Integration

In the RentMate settings UI, go to **Integrations → Email** and set:

- **Enabled**: on
- **Inbound address**: `agent@snoresidences.rentmate.io` (display only — for tenants to see)
- **Auto-spawn tasks**: on (so the agent handles emails autonomously)

---

## 6. Verify It Works

Send a test email to `agent@snoresidences.rentmate.io` from any email client.

In the Postmark dashboard → **Activity**, you should see it appear within seconds. In RentMate, a new conversation should appear under **Chats** with type `mirrored_chat`.

---

## Notes

- **Local dev testing** works without any DNS. Just `POST` a fake payload directly:
  ```bash
  RENTMATE_ENV=development  # set this to skip HMAC check
  curl -X POST http://localhost:8002/api/email/inbound \
    -H "Content-Type: application/json" \
    -d '{"FromFull":{"Email":"alice@example.com","Name":"Alice"},...}'
  ```
  See the plan file for a full example payload.

- **Per-org subdomains** (future): if each property management company gets their own address like `agent@smithproperties.rentmate.io`, add one MX record per subdomain, or use a wildcard `*.rentmate.io → inbound.postmarkapp.com` and route by the `To` address in the webhook handler.
