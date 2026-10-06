# Tutorial: a SaaS in an hour

This builds **Teamboard**, a small product with accounts, projects and
tasks that update live in every open tab, forms that check what people
type, a background job, a daily email and an admin, then ships it. It
uses each part of PyWeb once, in the order you'd meet them.

```bash
pip install "pyweb-stack[auth]"
pyweb new teamboard --template saas
cd teamboard
pyweb dev app.pyweb
```

Open http://localhost:8000 and sign up. The confirmation link is printed
in the terminal, because nothing is emailed while you develop. The rest
of this page walks through `app.pyweb` and then changes it.

## 1. The data

```python
class Project(Model):
    name: str = Field(min=2, max=60)
    about: str = Field(default="", max=500)
    owner: User = Field(readonly=True)


class Task(Model):
    title: str = Field(min=1, max=120)
    done: bool = False
    project: Project = Field(readonly=True)
    owner: User = Field(readonly=True)
```

Each class is a table, and each `Field` is a column with its rules
(`min`, `max`). `owner: User` is a foreign key to the auth kit's users.
`readonly=True` means a browser can never set it: the server decides who
owns a row.

The template came with `migrations/0001_initial.py`, and `pyweb dev`
applied it. See [Data & databases](09-data.md).

## 2. Who sees what

```python
def mine(model):
    return lambda user: model.owner_id == (user.id if user else -1)


Project.policy(read=mine(Project), write=lambda user, row: user is not None and row.owner_id == user.id)
```

A row policy applies to every query and save made while handling a
request. `Project.get_or_404(7)` finds project 7 only if it's yours.
Guessing someone else's id gets a 404, so an IDOR bug can't happen.

## 3. Server functions and forms

```python
@server(login=True)
def create_project(project: Project):
    project.owner = auth.user()
    project.save()
    welcome_tasks.enqueue(project.id)
    return project
```

```html
<Form action={create_project} redirect="/projects/{id}">
    <Input name="name" placeholder="Website relaunch" />
    <Textarea name="about" rows="2" />
    <Submit>Create project</Submit>
</Form>
```

The form takes its fields and rules from the `Project` Model. A one-letter
name is refused in the browser as you type, and again on the server. The
form also works without JavaScript, carries a CSRF token, and a double
click creates one project, not two. The whole function is one
transaction. See [Forms](23-forms.md).

## 4. Live pages

```python
tasks = Task.where(project_id=project.id).order("done", "id").live()
```

`.live()` makes the list update by itself. Open the project in two tabs,
add a task in one, and it appears in the other: only the changed row is
sent. See [Live data](21-live-data.md).

## 5. Background work

```python
@app.job(retries=3)
def welcome_tasks(project_id: int): ...


@app.cron("0 8 * * 1-5", tz="UTC")
def morning_digest(): ...
```

`welcome_tasks.enqueue(...)` inside `create_project` is queued in the
same transaction, so if the request fails, no job is queued. The job runs
after the commit, and failures are retried. The cron job emails everyone
their open tasks on weekday mornings, exactly once even with many
servers. See [Background jobs](24-jobs.md).

## 6. Change something

Add a due date to tasks. In `app.pyweb`:

```python
from datetime import date


class Task(Model):
    ...
    due: date | None = None


@server(login=True)
def add_task(project_id: int, title: str = Field(min=1, max=120), due: date | None = None):
    project = Project.get_or_404(project_id)
    return Task.create(title=title, due=due, project=project, owner_id=project.owner_id)
```

and in the project page's form:

```html
<Input name="due" label="" />
```

Save. Your editor (with the VS Code extension) shows "the Models changed
without a migration", with a lens that writes it. Or run:

```bash
pyweb db diff --name "task due dates"
pyweb db upgrade
```

The new input is a date picker, because the parameter is a `date`, and
the server rejects anything that isn't a date.

## 7. See what it does

The badge in the corner of every page is the dev toolbar. It shows each
request's time and SQL. If the project page ever reads `task.owner.name`
in a loop without `.include("owner")`, the toolbar and your editor both
flag the N+1 query and name the fix. See
[Logs, metrics & tracing](26-observability.md).

## 8. Test it

```bash
pytest
```

`test_app.py` signs in with `client.login("ann@example.com")`, creates
projects, runs the queued jobs and checks that Bob can't see Ann's
project. Add a test for each thing you change. `pyweb db check` in CI
fails if a Model changed without a migration.

## 9. Ship it

```bash
pyweb deploy compose --domain teamboard.example.com
```

The plan it prints explains each choice: Postgres instead of SQLite, a
separate worker for jobs, migrations before the new version starts, and
Caddy for HTTPS. It then writes the Dockerfile, `compose.yaml` and
`.env.example`. Fill in `.env` and run `docker compose up -d`. To use
`fly`, `render`, `railway` or `k8s`, pass that target instead. See
[Scaling & deploying](25-scaling.md).
