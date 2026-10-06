"""Example data: `pyweb db seed` (safe to run twice). The app's Models are available by name."""

from pyweb.db.seeds import seed


@seed
def demo_account():
    from pyweb.authkit import passwords
    user, _ = User.get_or_create(email="demo@example.com", defaults={  # noqa: F821 - provided by pyweb db seed
        "name": "Demo", "roles": ["admin"], "email_verified": True,
        "password_hash": passwords.hash_password("demo-password-123")})
    project, _ = Project.get_or_create(name="Launch", owner=user)  # noqa: F821
    for title in ("Pick a name", "Write the landing page", "Tell five friends"):
        Task.get_or_create(title=title, project=project, defaults={"owner": user})  # noqa: F821
