# Tutorial: a SaaS in an hour

This builds **Teamboard**, a small product where teams share projects
and tasks that update live in every open tab, with invitations and roles,
forms that check what people type, a background job, a webhook, a daily
email, API tokens and an admin, installable on phones and desktops. Then
it ships it. It uses each part of PyWeb once, in the order you'd meet them.

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
class Project(Model, tenant=True):
    name: str = Field(min=2, max=60)
    about: str = Field(default="", max=500)
    owner: User = Field(readonly=True)


class Task(Model, tenant=True):
    title: str = Field(min=1, max=120)
    done: bool = False
    project: Project = Field(readonly=True)
    owner: User = Field(readonly=True)
```

Each class is a table, and each `Field` is a column with its rules
(`min`, `max`). `owner: User` is a foreign key to the auth kit's users.
`readonly=True` means a browser can never set it: the server decides who
owns a row. `tenant=True` adds an `org` column: each project and task
belongs to a team.

The template came with `migrations/0001_initial.py`, and `pyweb dev`
applied it. See [Data & databases](09-data.md).

## 2. Teams, and who sees what

```python
auth = app.use_auth(orgs=True)
auth.plans({"free": {"seats": 5}, "pro": {"seats": 50}})


def my_team():
    team = auth.org()
    user = auth.user()
    if team is None and user is not None:
        team = auth.create_org(f"{user.name or user.email.split('@')[0]}'s team")
        auth.switch_org(team)
    return team
```

`orgs=True` adds organizations: `/orgs` lists yours, `/orgs/<slug>`
invites people by email and sets their roles (owner, admin, member,
viewer). The invitation email links to a page that adds the person to
the team once they sign in with that email.
Each new user gets a team of their own on their first visit, and the
sidebar shows its name. The free plan allows five seats.

While a request runs for a team, every query on a `tenant=True` Model
only sees that team's rows, and new rows belong to it.
`Project.get_or_404(7)` finds project 7 only if it's your team's.
Guessing another team's id gets a 404, so an IDOR bug can't happen. On
Postgres, row-level security enforces the same rule in the database. See
[Teams](30-teams.md).

## 3. Server functions and forms

```python
@server(login=True)
def create_project(project: Project):
    my_team()
    project.owner = auth.user()
    project.save()                                 # in the current team
    welcome_tasks.enqueue(project.id)
    webhooks.publish("project.created", {"id": project.id, "name": project.name})
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

`webhooks.publish(...)` sends a signed `project.created` event to every
endpoint the team registered (`WebhookEndpoint` rows), retried in the
background. People who'd rather call Teamboard from a script make an API
token at `/account/tokens` and call the same server functions with
`Authorization: Bearer ...`; `pyweb openapi app.pyweb` describes them.
See [Webhooks & files](33-webhooks-files.md).

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

## 6. The look

Teamboard is built from the UI kit (`from pyweb.ui import ...`):

```html
<Dialog>
    <DialogTrigger id="new-project">New project</DialogTrigger>
    <DialogContent>
        <DialogHeader><DialogTitle>New project</DialogTitle></DialogHeader>
        <Form action={create_project} redirect="/projects/{id}">...</Form>
    </DialogContent>
</Dialog>
```

- the layout is a `SidebarProvider` with your projects (and their open
  task counts) on the left and a `DropdownMenu` for your account;
- "New project" opens a `Dialog` around the same `<Form>`;
- `create_project` calls `toast("Project created", ...)` and the page
  shows it;
- each task row has `initial`, `animate`, `exit` and `layout`, so new
  tasks slide in, deleted ones slide out and the rest glide into place,
  in every open tab;
- `App(theme=Theme.preset("zinc"))` sets the colours, for the sign-in
  pages and `/admin` too;
- `pwa=True` makes it installable: browsers offer "Install", and the
  installed app opens in its own window with an offline page. See
  [Installable apps & push](31-pwa-push.md).

Change the theme in the dev toolbar (`pyweb dev`, then Theme) and save
it into `app.pyweb`. See [UI kit and motion](34-ui-and-motion.md).

## 7. Change something

Add a due date to tasks. In `app.pyweb`:

```python
from datetime import date


class Task(Model):
    ...
    due: date | None = None


@server(login=True)
def add_task(project_id: int, title: str = Field(min=1, max=120), due: date | None = None):
    project = Project.get_or_404(project_id)
    return Task.create(title=title, due=due, project=project, owner=auth.user())
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

## 8. See what it does

The badge in the corner of every page is the dev toolbar. It shows each
request's time and SQL. If the project page ever reads `task.owner.name`
in a loop without `.include("owner")`, the toolbar and your editor both
flag the N+1 query and name the fix. See
[Logs, metrics & tracing](26-observability.md).

## 9. Test it

```bash
pytest
```

`test_app.py` signs in with `client.login("ann@example.com")`, creates
projects, runs the queued jobs and checks that Bob, in another team,
can't see Ann's project, while `client.login("bob@example.com",
org="Rocket team", org_role="member")` makes him Ann's teammate, who can. Add a test for each thing you change. `pyweb db check` in CI
fails if a Model changed without a migration.

## 10. Ship it

```bash
pyweb deploy compose --domain teamboard.example.com
```

The plan it prints explains each choice: Postgres instead of SQLite, a
separate worker for jobs, migrations before the new version starts, and
Caddy for HTTPS. It then writes the Dockerfile, `compose.yaml` and
`.env.example`. Fill in `.env` and run `docker compose up -d`. To use
`fly`, `render`, `railway` or `k8s`, pass that target instead. See
[Scaling & deploying](25-scaling.md).
