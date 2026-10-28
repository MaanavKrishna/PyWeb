"""Stepper, dropping files on <FileInput>, and DataTable's CSV download in a real browser."""

import pytest

from pyweb import models as M
from pyweb.testing import serve

from .conftest import ready, until

APP = '''
from pyweb import App, Form, Input, FileInput, Field, server
from pyweb.forms import UploadedFile
from pyweb.models import Email
from pyweb.ui import Stepper, Step, DataTable, Image

app = App()

SEEN = []

@server
def sign_up(email: Email, name: str = Field(min=2), photos: list[UploadedFile] = []):
    SEEN.append(email + " " + ",".join(p.filename for p in photos))
    return {"name": name}

@app.page("/")
def Home():
    <Form action={sign_up} redirect="/done">
        <Stepper submit="Create account">
            <Step title="Account"><Input name="email" /></Step>
            <Step title="Profile">
                <Input name="name" />
                <FileInput name="photos" accept="image/*" />
            </Step>
        </Stepper>
    </Form>

@app.page("/done")
def Done():
    <p id="done">{SEEN[-1] if SEEN else ""}</p>

@app.page("/pics")
def Pics():
    shown = False

    def show():
        shown = True

    <button id="show" onclick={show}>Show</button>
    if shown:
        <Image id="pic" src="/static/cat.png" alt="A cat" width={300} height={200} />

@app.page("/table")
def Table():
    rows = [{"id": 1, "name": "Ada, Countess", "role": "admin"}, {"id": 2, "name": "Linus", "role": "user"}]
    <DataTable rows={rows} columns={[{"key": "name", "label": "Name"}, {"key": "role", "label": "Role"}]}
               export="csv" filename="people.csv" />
'''

PNG = (b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x01\\x08\\x06\\x00\\x00\\x00\\x1f\\x15\\xc4\\x89"
       b"\\x00\\x00\\x00\\rIDATx\\x9cc\\xf8\\x0f\\x00\\x00\\x01\\x01\\x00\\x05\\x18\\xd8N\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82")


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    (tmp_path / "app.pyweb").write_text(APP)
    with serve(str(tmp_path / "app.pyweb"), rate_limit=False) as url:
        yield url
    M._state.db = None


def test_stepper_checks_each_step_and_files_can_be_dropped(page, site):
    page.goto(site + "/")
    ready(page)
    page.wait_for_selector(".pw-stepper[data-pw-ui-ready]", state="attached")
    assert page.is_visible("#pw-sign_up-email") and not page.is_visible("#pw-sign_up-name")
    assert not page.is_visible("[data-part=submit]")
    page.click("[data-part=next]")                                       # empty email: stays
    until(page, "document.getElementById('pw-sign_up-email-error').hidden === false")
    assert not page.is_visible("#pw-sign_up-name")
    page.fill("#pw-sign_up-email", "ada@example.com")
    page.click("[data-part=next]")
    page.wait_for_selector("#pw-sign_up-name", state="visible")
    assert page.get_attribute("[aria-current=step]", "aria-current") == "step"
    assert page.inner_text("[aria-current=step]").endswith("Profile")
    # Drop two files (one not an image: left out) on the file field.
    page.evaluate("""(png) => {
        const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
        const dt = new DataTransfer();
        dt.items.add(new File([bytes], "cat.png", { type: "image/png" }));
        dt.items.add(new File(["hi"], "notes.txt", { type: "text/plain" }));
        const box = document.querySelector(".pw-fileinput");
        box.dispatchEvent(new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true }));
        box.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""", __import__("base64").b64encode(PNG).decode())
    page.wait_for_selector(".pw-file-list li")
    assert page.locator(".pw-file-list li").count() == 1
    assert "cat.png" in page.inner_text(".pw-file-list") and page.locator(".pw-file-list img").count() == 1
    page.fill("#pw-sign_up-name", "Ada")
    page.click("[data-part=submit]")
    page.wait_for_selector("#done")
    assert page.inner_text("#done") == "ada@example.com cat.png"


def test_data_table_downloads_csv(page, site):
    page.goto(site + "/table")
    ready(page)
    with page.expect_download() as info:
        page.click("[data-part=export]")
    download = info.value
    assert download.suggested_filename == "people.csv"
    text = open(download.path(), encoding="utf-8").read()
    assert text.splitlines() == ["Name,Role", '"Ada, Countess",admin', "Linus,user"]


def test_image_made_in_the_browser_has_its_srcset(page, site):
    page.goto(site + "/pics")
    ready(page)
    page.click("#show")
    page.wait_for_selector("#pic", state="attached")
    assert page.get_attribute("#pic", "srcset") == "/__pyweb/img/320/cat.png 320w, /__pyweb/img/640/cat.png 640w"
    assert page.get_attribute("#pic", "loading") == "lazy" and page.get_attribute("#pic", "alt") == "A cat"
