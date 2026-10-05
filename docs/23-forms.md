# Forms and uploads

`<Form>` builds a form from a server function. Its fields, rules and
labels come from the function's parameters (usually a Model), so you
write each rule once:

```pyweb
from pyweb import App, Checkbox, Field, Form, Input, Model, Select, Submit, Textarea, server

app = App(database="sqlite:///app.db")

class Tag(Model):
    name: str = Field(max=30)

class Post(Model):
    title: str = Field(min=3, max=120)
    body: str = ""
    tags: list[Tag] = []
    draft: bool = True

@server
def save_post(post: Post):
    post.save()
    return post

@app.page("/posts/new")
def NewPost():
    <Form action={save_post} redirect="/posts/{id}">
        <Input name="title" placeholder="A good title" />
        <Textarea name="body" rows="8" />
        <Select name="tags" />
        <Checkbox name="draft" label="Keep as draft" />
        <Submit>Publish</Submit>
    </Form>
```

What you get:

- **Labels, inputs and error slots**, wired up for screen readers
  (`label for`, `aria-invalid`, `aria-describedby`). Each field is wrapped
  in `<div class="pw-field pw-input">` (or `pw-textarea`, `pw-select`, ...);
  errors are `<p class="pw-error">`.
- **Checks in the browser** while the user types, using the Model's rules
  (`min`, `max`, `pattern`, `choices`, email/url/slug formats, required),
  with the same messages the server uses: "Title must be at least 3
  characters".
- **Submit without a page load.** The first field with a problem gets
  focus; errors from the server (including your `@validates` checks and
  unique fields: "Email is already taken") appear next to their input.
  `redirect="/posts/{id}"` goes to that page afterwards, filling `{...}`
  from what the function returned; without `redirect`, the form is
  cleared and fires a `pw:submitted` event with the result.
- **It works without JavaScript.** The same form is a normal `POST`: on a
  problem the page comes back with what was typed and the messages (HTTP
  422); on success the browser is redirected (303).
- **Safe by default.** Every form carries a CSRF token (tied to a cookie
  and `PYWEB_AUTH_SECRET`), refuses posts from other sites, and only reads
  the fields the action has: a browser can't set `id`, `private=True` or
  `readonly=True` fields. Each submit has a one-time key, so a double
  click or a retried request runs the action once and gets the same answer.
- **One transaction.** The action runs like every server function: its
  writes commit together or not at all.

## Fields

| Tag | For | Notes |
|---|---|---|
| `<Input name="...">` | text, numbers, dates, email, url | the input type follows the field (`int` → number, `datetime` → datetime-local, `Email` → email) |
| `<Textarea name="...">` | long text | |
| `<Select name="...">` | `choices=`, `Literal[...]`, foreign keys, many-to-many | options come from the choices or the related rows (up to 1000); many-to-many is a multiple select; or write your own `<option>`s inside |
| `<Checkbox name="...">` | `bool` | unticked means `False` |
| `<FileInput name="...">` | `File(...)` fields, `UploadedFile` parameters | see Uploads |
| `<Submit>Text</Submit>` | the button | disabled while sending |
| `<FormError />` | messages that aren't about one field | added at the top automatically if you don't place it |

`label="..."` replaces the label (it defaults to the field's `label=` or
its name: `published_at` → "Published at"). `class=` goes on the field's
wrapper; other attributes (`placeholder`, `rows`, `autocomplete`, ...) go
on the input.

## Editing a row

Pass the row as `values=`:

```pyweb
from pyweb import Checkbox, Form, Input, Submit

@app.page("/posts/{post_id}/edit")
def EditPost(post_id: int):
    post = Post.get_or_404(post_id)
    <Form action={save_post} values={post}>
        <Input name="title" />
        <Checkbox name="draft" />
        <Submit>Save</Submit>
    </Form>
```

The form shows the row's values and carries its id, signed so it can't be
changed in the browser. The action receives the stored row with the
submitted fields applied (fields not in the form keep their values), and
`post.save()` updates it. Still check that the current user may edit the
row in your action: the signature only proves the form came from your page.

## Plain parameters

Without a Model parameter, each parameter is a field:

```python
from typing import Literal
from pyweb.models import Email

@server
def subscribe(email: Email, plan: Literal["free", "pro"] = "free", daily: bool = False):
    ...
```

`Annotated[str, Field(max=40)]` adds rules to a parameter. Parameters with
defaults (or `| None`) are optional.

## Errors from your code

Raise `ValidationError` for problems you find yourself:

```python
from pyweb import ValidationError

@server
def save_post(post: Post):
    if Post.where(title=post.title).exists():
        raise ValidationError({"title": "is already used"})
    if not allowed(post):
        raise ValidationError("You can't publish yet")      # shown at the top of the form
    post.save()
```

Messages starting with a lowercase letter are shown after the field's
label ("Title is already used").

## Uploads

```python
from pyweb.models import File

class User(Model):
    avatar: str | None = File(types=["image/*"], max_size="2MB")
```

```
<FileInput name="avatar" />
```

- The file's type is read from its bytes, not its name or what the
  browser claims; HTML and SVG never count as images.
- `max_size` is checked before saving; the whole submit is limited by
  `PYWEB_MAX_UPLOAD` (default 10MB, 413 beyond it).
- Files are stored under random names; the column keeps the key.
  `storage.url(user.avatar)` (from `pyweb`) gives a link.
- A form with no new file keeps the old one.
- Plain parameters can take the file itself: `def upload(doc: UploadedFile)`
  (`.filename`, `.content_type`, `.size`, `.data`, `.save()`).

Where files go is set by `PYWEB_STORAGE`:

| Value | Storage |
|---|---|
| `file://./uploads` (default) | a folder on the server; served from `/__pyweb/files/...` with `nosniff`, a sandboxing CSP, and `Content-Disposition: attachment` for anything that isn't media, PDF or text |
| `s3://bucket/prefix` | S3 or any S3-compatible store (`S3_ENDPOINT` for R2, MinIO, Spaces); signed with `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`, no SDK needed |

With S3, `storage.storage().upload_url(filename)` returns a key and a
presigned URL, so large files can go from the browser straight to the
bucket.

## Multi-step forms

Carry earlier steps' answers in a signed hidden value:

```python
from pyweb import forms

token = forms.carry({"email": email, "plan": plan})    # put in a hidden input on the next step
data = forms.restore(token)                             # None if changed or older than an hour
```

## Without `<Form>`

Every server function also has a JSON endpoint (see
[Server functions](06-server-functions.md)), and Models validate on
`save()`, so you can build any form by hand with `bind=` and an
`onclick` handler.
