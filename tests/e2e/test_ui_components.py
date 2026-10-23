"""The rest of the UI kit in a real browser: tabs, accordions, form controls, layout and navigation,
calendars, carousels, data tables and charts (keyboard maps from the WAI-ARIA Authoring Practices)."""

import textwrap

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready, until


def _ui_ready(page, selector):
    page.wait_for_selector(selector + "[data-pw-ui-ready]", state="attached")


def _open(page, tmp_path, source):
    path = tmp_path / "app.pyweb"
    path.write_text(textwrap.dedent(source))
    return serve(str(path))


DISCLOSURE = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Disclosure")


    @app.page("/")
    def Home():
        tab = "b"
        seen = ""

        def pick(e):
            tab = e.detail["value"]

        def opened(e):
            seen = ",".join(e.detail["value"])
        <main>
            <Tabs id="tabs" default="one">
                <TabsList>
                    <TabsTrigger value="one">One</TabsTrigger>
                    <TabsTrigger value="two">Two</TabsTrigger>
                    <TabsTrigger value="off" disabled={True}>Off</TabsTrigger>
                    <TabsTrigger value="three">Three</TabsTrigger>
                </TabsList>
                <TabsContent value="one">First panel</TabsContent>
                <TabsContent value="two">Second panel
                    <Popover><PopoverTrigger id="inner">Inner</PopoverTrigger><PopoverContent>pop</PopoverContent></Popover>
                </TabsContent>
                <TabsContent value="off">Never</TabsContent>
                <TabsContent value="three">Third panel</TabsContent>
            </Tabs>
            <Tabs id="ctl" value={tab} activation="manual" onvaluechange={pick}>
                <TabsList>
                    <TabsTrigger value="a">A</TabsTrigger>
                    <TabsTrigger value="b">B</TabsTrigger>
                </TabsList>
                <TabsContent value="a">Panel A</TabsContent>
                <TabsContent value="b">Panel B</TabsContent>
            </Tabs>
            <p id="tab">{tab}</p>
            <Accordion id="acc" type="single" default="x">
                <AccordionItem value="x">
                    <AccordionTrigger>X?</AccordionTrigger>
                    <AccordionContent>Answer X</AccordionContent>
                </AccordionItem>
                <AccordionItem value="y">
                    <AccordionTrigger>Y?</AccordionTrigger>
                    <AccordionContent>Answer Y</AccordionContent>
                </AccordionItem>
                <AccordionItem value="z" disabled={True}>
                    <AccordionTrigger>Z?</AccordionTrigger>
                    <AccordionContent>Answer Z</AccordionContent>
                </AccordionItem>
            </Accordion>
            <Accordion id="multi" type="multiple" onvaluechange={opened}>
                <AccordionItem value="p"><AccordionTrigger>P</AccordionTrigger><AccordionContent>pp</AccordionContent></AccordionItem>
                <AccordionItem value="q"><AccordionTrigger>Q</AccordionTrigger><AccordionContent>qq</AccordionContent></AccordionItem>
            </Accordion>
            <p id="seen">{seen}</p>
            <Collapsible id="col">
                <CollapsibleTrigger>More</CollapsibleTrigger>
                <CollapsibleContent>Hidden text</CollapsibleContent>
            </Collapsible>
        </main>
'''


def test_tabs_accordions_and_collapsibles(page, tmp_path):
    with _open(page, tmp_path, DISCLOSURE) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#tabs")
        tabs = page.locator("#tabs [role=tab]")
        expect(tabs.nth(0)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_text("Second panel")).to_be_hidden()
        panel = page.locator("#tabs > [role=tabpanel]").nth(0)
        expect(panel).to_have_attribute("aria-labelledby", tabs.nth(0).get_attribute("id"))
        tabs.nth(0).focus()
        page.keyboard.press("ArrowRight")                          # automatic activation
        expect(tabs.nth(1)).to_be_focused()
        expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_text("Second panel")).to_be_visible()
        expect(page.get_by_text("First panel")).to_be_hidden()
        page.keyboard.press("ArrowRight")                          # skips the disabled tab
        expect(tabs.nth(3)).to_be_focused()
        page.keyboard.press("ArrowRight")                          # wraps
        expect(tabs.nth(0)).to_be_focused()
        page.keyboard.press("End")
        expect(page.get_by_text("Third panel")).to_be_visible()
        # A popover inside a panel keeps its own trigger: it isn't a tab.
        assert page.locator("#inner").get_attribute("role") is None
        assert page.locator("#tabs > [role=tabpanel]").count() == 4
        # Controlled, manual activation: arrows move focus, Enter chooses, the app's state decides.
        _ui_ready(page, "#ctl")
        ctl = page.locator("#ctl [role=tab]")
        expect(ctl.nth(1)).to_have_attribute("aria-selected", "true")
        ctl.nth(1).focus()
        page.keyboard.press("ArrowLeft")
        expect(ctl.nth(0)).to_be_focused()
        expect(ctl.nth(1)).to_have_attribute("aria-selected", "true")
        page.keyboard.press("Enter")
        expect(page.locator("#tab")).to_have_text("a")
        expect(ctl.nth(0)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_text("Panel A")).to_be_visible()

        _ui_ready(page, "#acc")
        items = page.locator("#acc > details")
        expect(items.nth(0)).to_have_attribute("open", "")
        page.click("#acc summary >> nth=1")
        expect(items.nth(1)).to_have_attribute("data-state", "open")
        until(page, "!document.querySelectorAll('#acc > details')[0].open")   # single: the other one closes
        expect(page.get_by_text("Answer Y")).to_be_visible()
        page.click("#acc summary >> nth=2")                        # disabled
        expect(items.nth(2)).to_have_attribute("data-state", "closed")
        page.locator("#acc summary").nth(1).focus()
        page.keyboard.press("ArrowDown")
        expect(page.locator("#acc summary").nth(2)).to_be_focused()
        page.keyboard.press("Home")
        expect(page.locator("#acc summary").nth(0)).to_be_focused()

        _ui_ready(page, "#multi")
        page.click("#multi summary >> nth=0")
        page.click("#multi summary >> nth=1")
        expect(page.locator("#seen")).to_have_text("p,q")          # multiple stay open
        page.click("#multi summary >> nth=0")
        expect(page.locator("#seen")).to_have_text("q")

        _ui_ready(page, "#col")
        expect(page.get_by_text("Hidden text")).to_be_hidden()
        page.click("#col summary")
        expect(page.get_by_text("Hidden text")).to_be_visible()
        page.click("#col summary")
        until(page, "!document.querySelector('#col').open")


CONTROLS = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Controls")


    @app.page("/")
    def Home():
        align = "left"
        code = ""
        bold = False

        def set_align(e):
            align = e.detail["value"]

        def done(e):
            code = e.detail["value"]

        def pressed(e):
            bold = e.detail["pressed"]
        <form id="f" method="post">
            <Checkbox id="cb" label="Some" name="cb" indeterminate={True} />
            <Switch id="sw" label="Emails" name="sw" />
            <RadioGroup id="rg" name="plan" value="pro" options={[("free", "Free"), ("pro", "Pro"), ("team", "Team")]} />
            <Toggle id="tg" label="Bold" onpressedchange={pressed}>B</Toggle>
            <p id="bold">{bold}</p>
            <ToggleGroup id="single" type="single" value={align} onvaluechange={set_align} name="align">
                <ToggleGroupItem value="left" label="Left">L</ToggleGroupItem>
                <ToggleGroupItem value="center" label="Center">C</ToggleGroupItem>
                <ToggleGroupItem value="right" label="Right">R</ToggleGroupItem>
            </ToggleGroup>
            <p id="align">{align}</p>
            <ToggleGroup id="multi" type="multiple" default={["b"]}>
                <ToggleGroupItem value="b" label="Bold">B</ToggleGroupItem>
                <ToggleGroupItem value="i" label="Italic">I</ToggleGroupItem>
            </ToggleGroup>
            <Slider id="sl" name="vol" value={20} label="Volume" show_value={True} />
            <InputOTP id="otp" name="code" length={4} oncomplete={done} />
            <p id="code">{code}</p>
        </form>
'''


def test_form_controls(page, tmp_path):
    with _open(page, tmp_path, CONTROLS) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#cb")
        assert page.eval_on_selector("#cb", "el => el.indeterminate")
        page.click("#cb")
        assert page.eval_on_selector("#cb", "el => el.checked && !el.indeterminate")
        expect(page.locator("#sw")).to_have_attribute("role", "switch")
        page.click("text=Emails")
        expect(page.locator("#sw")).to_be_checked()

        _ui_ready(page, "#rg")
        radios = page.locator("#rg input[type=radio]")
        expect(radios.nth(1)).to_be_checked()
        radios.nth(1).focus()
        page.keyboard.press("ArrowDown")                           # native radio keyboard
        expect(radios.nth(2)).to_be_checked()
        expect(page.locator("#rg")).to_have_attribute("data-value", "team")

        _ui_ready(page, "#tg")
        page.click("#tg")
        expect(page.locator("#tg")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#bold")).to_have_text("True")

        _ui_ready(page, "#single")
        group = page.locator("#single [data-part=item]")
        expect(page.locator("#single")).to_have_attribute("role", "radiogroup")
        expect(group.nth(0)).to_have_attribute("aria-checked", "true")
        group.nth(0).focus()
        page.keyboard.press("ArrowRight")
        expect(group.nth(1)).to_be_focused()
        expect(page.locator("#align")).to_have_text("center")
        expect(group.nth(1)).to_have_attribute("aria-checked", "true")
        assert page.eval_on_selector("#single input[type=hidden]", "el => el.value") == "center"

        _ui_ready(page, "#multi")
        multi = page.locator("#multi [data-part=item]")
        expect(multi.nth(0)).to_have_attribute("aria-pressed", "true")
        multi.nth(1).click()
        expect(multi.nth(1)).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#multi")).to_have_attribute("data-value", "b,i")
        multi.nth(0).click()
        expect(page.locator("#multi")).to_have_attribute("data-value", "i")

        _ui_ready(page, ".pw-slider")
        fill = "document.querySelector('.pw-slider').style.getPropertyValue('--pw-fill').trim()"
        assert page.evaluate(fill) == "20%"
        page.locator("#sl").focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator(".pw-slider output")).to_have_text("21")
        assert page.evaluate(fill) == "21%"

        _ui_ready(page, ".pw-otp")
        otp = page.locator(".pw-otp input")
        otp.focus()
        page.keyboard.type("12a3")                                 # letters are dropped
        slots = page.locator(".pw-otp [data-part=slot]")
        expect(slots.nth(2)).to_have_text("3")
        page.keyboard.type("4")
        expect(page.locator("#code")).to_have_text("1234")
        assert otp.input_value() == "1234"


LAYOUT = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Layout")


    @app.page("/")
    def Home():
        page = 3

        def setp(n):
            page = n
        <SidebarProvider id="sb">
            <Sidebar>
                <SidebarHeader>Acme</SidebarHeader>
                <SidebarContent>
                    <SidebarGroup label="Work">
                        <SidebarMenu>
                            <SidebarMenuItem><SidebarMenuButton href="#home" active={True} icon="⌂">Home</SidebarMenuButton></SidebarMenuItem>
                            <SidebarMenuItem>
                                <DropdownMenu>
                                    <DropdownMenuTrigger id="menu-in-sidebar">Menu</DropdownMenuTrigger>
                                    <DropdownMenuContent><MenuItem>One</MenuItem></DropdownMenuContent>
                                </DropdownMenu>
                            </SidebarMenuItem>
                        </SidebarMenu>
                    </SidebarGroup>
                </SidebarContent>
            </Sidebar>
            <SidebarInset>
                <SidebarTrigger id="toggle" />
                <NavigationMenu id="nav">
                    <NavigationMenuList>
                        <NavigationMenuItem>
                            <NavigationMenuTrigger>Docs</NavigationMenuTrigger>
                            <NavigationMenuContent>
                                <NavigationMenuLink href="#intro" description="Start here">Intro</NavigationMenuLink>
                                <NavigationMenuLink href="#api">API</NavigationMenuLink>
                            </NavigationMenuContent>
                        </NavigationMenuItem>
                        <NavigationMenuItem>
                            <NavigationMenuTrigger>Blog</NavigationMenuTrigger>
                            <NavigationMenuContent><NavigationMenuLink href="#post">Post</NavigationMenuLink></NavigationMenuContent>
                        </NavigationMenuItem>
                        <NavigationMenuItem><NavigationMenuLink href="#about">About</NavigationMenuLink></NavigationMenuItem>
                    </NavigationMenuList>
                </NavigationMenu>
                <div style="height: 200px; width: 600px">
                    <ResizablePanelGroup id="rz" save="test">
                        <ResizablePanel size={30} min={20} max={70}>Files</ResizablePanel>
                        <ResizableHandle grip={True} />
                        <ResizablePanel size={70}>Editor</ResizablePanel>
                    </ResizablePanelGroup>
                </div>
                <Pagination id="pg" page={page} pages={10} onchange={setp} />
                <p id="page">{page}</p>
            </SidebarInset>
        </SidebarProvider>
'''


def test_sidebar_navigation_resizable_and_pagination(page, tmp_path):
    with _open(page, tmp_path, LAYOUT) as url:
        page.set_viewport_size({"width": 1200, "height": 800})
        page.goto(url)
        page.evaluate("localStorage.clear()")
        page.reload()
        ready(page)
        _ui_ready(page, "#sb")
        sb = page.locator("#sb")
        expect(sb).to_have_attribute("data-state", "expanded")
        expect(page.locator("#toggle")).to_have_attribute("aria-expanded", "true")
        page.click("#toggle")
        expect(sb).to_have_attribute("data-state", "collapsed")
        until(page, "document.querySelector('.pw-sidebar').getBoundingClientRect().width < 60")
        page.keyboard.press("Control+b")
        expect(sb).to_have_attribute("data-state", "expanded")
        page.keyboard.press("Control+b")
        page.reload()                                              # remembered
        ready(page)
        _ui_ready(page, "#sb")
        expect(sb).to_have_attribute("data-state", "collapsed")
        page.keyboard.press("Control+b")
        # A menu inside the sidebar keeps its own trigger.
        _ui_ready(page, "#sb [data-pw-ui=menu]")
        expect(page.locator("#menu-in-sidebar")).to_have_attribute("aria-haspopup", "menu")
        page.click("#menu-in-sidebar")
        expect(page.get_by_role("menuitem", name="One")).to_be_visible()
        expect(sb).to_have_attribute("data-state", "expanded")
        page.keyboard.press("Escape")

        _ui_ready(page, "#nav")
        triggers = page.locator("#nav [data-part=trigger]")
        triggers.nth(0).hover()
        expect(page.get_by_text("Start here")).to_be_visible()
        expect(triggers.nth(0)).to_have_attribute("aria-expanded", "true")
        triggers.nth(1).hover()                                    # moving across swaps at once
        expect(page.get_by_role("link", name="Post")).to_be_visible()
        expect(page.get_by_text("Start here")).to_be_hidden()
        page.mouse.move(5, 790)
        expect(page.get_by_role("link", name="Post")).to_be_hidden()
        triggers.nth(0).focus()
        page.keyboard.press("ArrowRight")
        expect(triggers.nth(1)).to_be_focused()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("link", name="About")).to_be_focused()
        page.keyboard.press("Home")
        page.keyboard.press("ArrowDown")
        expect(page.get_by_role("link", name="Intro")).to_be_focused()
        page.keyboard.press("Escape")
        expect(triggers.nth(0)).to_be_focused()
        expect(page.get_by_text("Start here")).to_be_hidden()

        _ui_ready(page, "#rz")
        handle = page.locator("#rz [data-part=handle]")
        expect(handle).to_have_attribute("aria-valuenow", "30")
        handle.focus()
        page.keyboard.press("ArrowRight")
        expect(handle).to_have_attribute("aria-valuenow", "32")
        page.keyboard.press("End")
        expect(handle).to_have_attribute("aria-valuenow", "70")    # max
        page.keyboard.press("Home")
        expect(handle).to_have_attribute("aria-valuenow", "20")    # min
        box = handle.bounding_box()
        page.mouse.move(box["x"] + 1, box["y"] + 50)
        page.mouse.down()
        page.mouse.move(box["x"] + 181, box["y"] + 50, steps=5)
        page.mouse.up()
        now = int(handle.get_attribute("aria-valuenow"))
        assert 45 <= now <= 52, now
        assert page.evaluate("JSON.parse(localStorage.getItem('pyweb:resizable:test'))[0]") >= 45

        pages = page.locator("#pg [data-part=page]")
        expect(pages.filter(has_text="3")).to_have_attribute("aria-current", "page")
        page.click("#pg [data-part=next]")
        expect(page.locator("#page")).to_have_text("4")
        page.click("#pg [data-part=page] >> text=10")
        expect(page.locator("#page")).to_have_text("10")
        expect(page.locator("#pg [data-part=next]")).to_be_disabled()


def test_sidebar_is_a_sheet_on_small_screens(page, tmp_path):
    with _open(page, tmp_path, LAYOUT) as url:
        page.set_viewport_size({"width": 500, "height": 800})
        page.goto(url)
        ready(page)
        _ui_ready(page, "#sb")
        sb = page.locator("#sb")
        expect(sb).to_have_attribute("data-mobile", "true")
        assert page.eval_on_selector(".pw-sidebar", "el => el.inert")
        page.click("#toggle")
        expect(sb).to_have_attribute("data-mobile-open", "true")
        page.wait_for_timeout(300)
        assert page.eval_on_selector(".pw-sidebar", "el => el.getBoundingClientRect().left") >= 0
        page.keyboard.press("Escape")
        expect(sb).to_have_attribute("data-mobile-open", "false")
        page.click("#toggle")
        page.click(".pw-sidebar-button")                           # following a link closes it
        expect(sb).to_have_attribute("data-mobile-open", "false")


DATES = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Dates")


    @app.page("/")
    def Home():
        picked = ""

        def changed(e):
            picked = e.detail["value"]
        <main>
            <Calendar id="cal" name="day" value="2026-10-14" min="2026-10-05" week_start={1} locale="en-US"
                      onvaluechange={changed} />
            <p id="picked">{picked}</p>
            <DatePicker id="dp" name="due" locale="en-US" week_start={0} />
            <Calendar id="range" mode="range" value="2026-03-02/2026-03-05" locale="en-US" />
        </main>
'''


def test_calendar_and_date_picker(page, tmp_path):
    with _open(page, tmp_path, DATES) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#cal")
        cal = page.locator("#cal")
        expect(cal.locator("[data-part=caption]")).to_have_text("October 2026")
        expect(cal.locator("th").first).to_have_text("Mo")        # week_start=1
        day = cal.locator("[tabindex='0']")
        expect(day).to_have_attribute("data-date", "2026-10-14")
        expect(cal.locator("[aria-selected=true] button")).to_have_attribute("data-date", "2026-10-14")
        day.focus()
        page.keyboard.press("ArrowDown")
        expect(page.locator("#cal [data-date='2026-10-21']")).to_be_focused()
        page.keyboard.press("PageDown")
        expect(cal.locator("[data-part=caption]")).to_have_text("November 2026")
        expect(page.locator("#cal [data-date='2026-11-21']")).to_be_focused()
        page.keyboard.press("PageUp")
        page.keyboard.press("Home")                                # start of the week (Monday)
        expect(page.locator("#cal [data-date='2026-10-19']")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#picked")).to_have_text("2026-10-19")
        assert cal.locator("input[name=day]").input_value() == "2026-10-19"
        expect(page.locator("#cal [data-date='2026-10-04']")).to_be_disabled()   # before min
        expect(cal.locator("[data-part=previous]")).to_be_disabled()

        _ui_ready(page, "#dp")
        trigger = page.locator("#dp [data-part=trigger]")
        expect(trigger).to_have_text("Pick a date")
        trigger.click()
        dp_cal = page.locator("#dp .pw-calendar")
        expect(dp_cal).to_be_visible()
        expect(dp_cal.locator("th").first).to_have_text("Su")
        until(page, "!!(document.activeElement && document.activeElement.dataset.date)")   # a frame later
        focused = page.evaluate("document.activeElement.dataset.date")
        assert focused, "the picker focuses a day"
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
        expect(dp_cal).to_be_hidden()
        expect(trigger).to_be_focused()
        value = page.locator("#dp input[name=due]").input_value()
        assert len(value) == 10 and value != focused
        assert "Pick a date" not in trigger.inner_text()

        rng = page.locator("#range")
        _ui_ready(page, "#range")
        expect(rng.locator("[data-in-range]")).to_have_count(2)
        rng.locator("[data-date='2026-03-10']").click()
        rng.locator("[data-date='2026-03-08']").click()            # before the start: they swap
        assert rng.locator("input").input_value() == "2026-03-08/2026-03-10"


CAROUSEL = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Carousel")


    @app.page("/")
    def Home():
        <div style="width: 400px; margin: 40px">
            <Carousel id="car" label="Photos">
                <CarouselContent>
                    for i in range(4):
                        <CarouselItem><div style="height: 100px">Slide {i + 1}</div></CarouselItem>
                </CarouselContent>
                <CarouselPrevious />
                <CarouselNext />
                <CarouselDots />
            </Carousel>
        </div>
'''


def test_carousel(page, tmp_path):
    with _open(page, tmp_path, CAROUSEL) as url:
        page.emulate_media(reduced_motion="reduce")
        page.goto(url)
        ready(page)
        _ui_ready(page, "#car")
        car = page.locator("#car")
        expect(car).to_have_attribute("aria-roledescription", "carousel")
        slides = car.locator("[data-part=item]")
        expect(slides.nth(0)).to_have_attribute("aria-label", "1 of 4")
        expect(car.locator("[data-part=previous]")).to_be_disabled()
        car.locator("[data-part=next]").click()
        expect(slides.nth(1)).to_have_attribute("data-state", "active")
        expect(car.locator("[data-part=dot]").nth(1)).to_have_attribute("aria-current", "true")
        car.locator("[data-part=dot]").nth(3).click()
        expect(slides.nth(3)).to_have_attribute("data-state", "active")
        expect(car.locator("[data-part=next]")).to_be_disabled()
        car.focus()
        page.keyboard.press("ArrowLeft")
        expect(slides.nth(2)).to_have_attribute("data-state", "active")


DATA = '''
    from pyweb import App
    from pyweb.ui import *

    app = App(title="Data")


    @app.page("/")
    def Home():
        extra = 0
        chosen = ""

        def add():
            extra += 1

        def picked(keys):
            chosen = ",".join(str(k) for k in sorted(keys))
        rows = [{"id": i, "name": "Task " + str(i), "points": (i * 7) % 11, "done": i % 3 == 0}
                for i in range(1, 24 + extra)]
        sales = [{"m": "Jan", "a": 12, "b": 5}, {"m": "Feb", "a": 19, "b": 8 + extra * 10}]
        <main>
            <button id="add" onclick={add}>add</button>
            <DataTable id="dt" rows={rows} page_size={5} selectable={True} onselect={picked} columns={[
                "name",
                {"key": "points", "label": "Pts", "align": "end"},
                {"key": "done", "format": lambda v: "yes" if v else "", "sortable": False},
            ]} />
            <p id="chosen">{chosen}</p>
            <BarChart id="bars" data={sales} x="m" y={["a", "b"]} label="Sales" />
            <LineChart id="line" data={sales} x="m" y="a" area={True} />
            <PieChart id="pie" data={sales} name="m" value="b" donut={True} />
            <Sparkline id="spark" values={[r["points"] for r in rows]} />
        </main>
'''


def test_data_table_and_charts(page, tmp_path):
    with _open(page, tmp_path, DATA) as url:
        page.goto(url)
        ready(page)
        dt = page.locator("#dt")
        body = dt.locator("tbody tr")
        expect(body).to_have_count(5)
        expect(dt.locator("[data-part=footer] span").first).to_have_text("1–5 of 23")
        dt.locator("[data-part=search]").fill("task 2")
        expect(body).to_have_count(5)                              # 2, 20, 21, 22, 23
        expect(dt.locator("[data-part=footer] span").first).to_have_text("1–5 of 5")
        dt.locator("[data-part=search]").fill("")
        dt.locator("button:has-text('Pts')").click()               # ascending
        expect(dt.locator("th[aria-sort=ascending]")).to_have_count(1)
        expect(body.nth(0).locator("td").nth(2)).to_have_text("0")
        dt.locator("button:has-text('Pts')").click()               # descending
        expect(body.nth(0).locator("td").nth(2)).to_have_text("10")
        dt.locator("button:has-text('Pts')").click()               # unsorted
        expect(dt.locator("th[aria-sort]")).to_have_count(0)
        dt.locator("button:has-text('Next')").click()
        expect(body.nth(0).locator("td").nth(1)).to_have_text("Task 6")
        body.nth(0).locator("input").click()
        expect(page.locator("#chosen")).to_have_text("6")
        expect(body.nth(0)).to_have_attribute("data-state", "selected")
        expect(dt.locator("thead input")).to_have_attribute("data-indeterminate", "true")
        dt.locator("thead input").click()                          # the whole page
        expect(page.locator("#chosen")).to_have_text("6,7,8,9,10")
        dt.locator("thead input").click()
        expect(page.locator("#chosen")).to_have_text("")
        expect(dt.locator("th >> text=Done")).to_have_count(1)     # not sortable: plain text
        page.click("#add")                                         # rows follow their source
        expect(dt.locator("[data-part=footer] span").first).to_have_text("6–10 of 24")

        bars = page.locator("#bars rect[data-part=bar]")
        expect(bars).to_have_count(4)
        before = bars.nth(3).get_attribute("height")
        page.click("#add")
        expect(bars.nth(3)).not_to_have_attribute("height", before)  # the chart redraws
        expect(page.locator("#bars [data-part=legend] li")).to_have_count(2)
        expect(page.locator("#line path[data-part=area]")).to_have_count(1)
        expect(page.locator("#pie path[data-part=slice]")).to_have_count(2)
        assert page.locator("#bars svg").get_attribute("aria-label") == "Sales"
