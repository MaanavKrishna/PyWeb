"""The `pyweb new` template names (kept apart so the CLI can list them without loading the MCP server)."""

TEMPLATES = ("blank", "counter", "todo", "blog", "saas", "auth", "chat", "ai-chat", "desktop")

DESCRIPTIONS = {
    "blank": "one empty page",
    "counter": "a page with a button: the smallest app with state",
    "todo": "a list you add to and tick off, with the UI kit",
    "blog": "pages with routes, a database and server functions",
    "saas": "teams, sign-in, data, background jobs and an admin",
    "auth": "accounts: sign up, sign in, passkeys",
    "chat": "a live chat room (realtime)",
    "ai-chat": "a streaming chat with an AI model",
    "desktop": "a notes app in its own window",
}
