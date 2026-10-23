# Webhooks and file storage

## Receiving webhooks

Payment providers, GitHub, email services and most other platforms tell
your app about events by POSTing to a URL. Declare it:

```python
from pyweb import App, webhooks

app = App(database="sqlite:///app.db")


@app.webhook("/hooks/stripe", secret=webhooks.env_secret("STRIPE_WEBHOOK_SECRET"), scheme="stripe")
def on_stripe(event):
    if event.type == "invoice.paid":
        mark_paid(event.data["data"]["object"]["id"])
```

For each delivery PyWeb:

1. checks the signature in constant time, and for schemes that sign a
   timestamp, that it's less than 5 minutes old (a captured delivery can't
   be replayed later). Anything else gets `401`;
2. drops a delivery it has already seen (by the event's id), answering
   `200`, so a provider's retry or a replay runs your code once;
3. answers `202` straight away and runs your function as a
   [background job](24-jobs.md), so slow work doesn't make the provider
   time out, and a failure is retried with backoff.

`event` has `.id`, `.type`, `.data` (the parsed JSON body), `.headers` (a
few useful ones) and `.source` (the path).

| `scheme` | Used by | Signature |
|---|---|---|
| `"github"` | GitHub | `X-Hub-Signature-256` (HMAC-SHA256 of the body) |
| `"stripe"` | Stripe | `Stripe-Signature` (timestamp and HMAC) |
| `"standard"` | Svix, Clerk, Resend, Liveblocks and other [Standard Webhooks](https://www.standardwebhooks.com) senders | `webhook-id`, `webhook-timestamp`, `webhook-signature` |

`secret=` takes the string, or `webhooks.env_secret("NAME")` to read it
from the environment when a delivery arrives.

### Testing a receiver

Sign a delivery the way the sender would and post it with the test
client; the handler then runs as a job:

```python
import json, time
from pyweb import jobs
from pyweb.webhooks import sign_standard

def test_new_order_webhook(client):
    body = json.dumps({"type": "order.created", "data": {"id": 7}}).encode()
    ts = str(int(time.time()))
    res = client.request("POST", "/hooks/orders", body, {
        "Content-Type": "application/json", "webhook-id": "msg_1", "webhook-timestamp": ts,
        "webhook-signature": sign_standard("whsec_...", "msg_1", ts, body)})
    assert res.status == 202
    assert jobs.Worker(schedule=False).drain() == 1
```

An AI assistant can do the same with the `pyweb_webhook_send` MCP tool,
which signs for whichever scheme the receiver expects, runs the job and
reports its result (and with `bad_signature`, checks that forgeries are
refused).

## Sending webhooks

```python
from pyweb import webhooks

webhooks.send("https://customer.example/hooks", "invoice.paid", {"id": 42}, secret=endpoint.secret)
```

Deliveries are signed with the Standard Webhooks scheme (so receivers can
verify them with any of its libraries), sent by background jobs through
[`pyweb.http.fetch`](13-security.md#requests-your-server-makes) (so a
customer can't point an endpoint at your internal network), tried up to 8
times with growing delays (about a day in all), and logged in the
`pyweb_webhook_deliveries` table with each attempt's status and error.
`webhooks.new_secret()` makes a secret to give the receiver.

With [organizations](30-teams.md), keep each organization's endpoints in
`WebhookEndpoint` rows (`org_id`, `url`, `secret`, `events`) and publish:

```python
webhooks.publish("invoice.paid", {"id": 42})    # every endpoint of the current organization subscribed to it
```

## Cleaning up uploaded files

Every file uploaded through a form (`File` fields) is recorded in
PyWeb's `pyweb_files` table. Once a day, apps with `File` fields delete
files that no row refers to any more and that are more than a day old:
the old avatar after a new one was uploaded, the attachment of a deleted
message, an upload whose form was never saved. This works the same for a
local folder and for S3.

```bash
pyweb files clean --dry-run      # what would be deleted
pyweb files clean                # delete them now
```
