"""The UI kit in a real browser: attribute pass-through, kit components hydrating, and (as the kit
grows) every primitive's keyboard behaviour."""

import textwrap

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready

SPREAD = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import Button, Badge

    app = App(title="Spread")


    def Field(label, **attrs):
        <label>{label} <input class="in" {**attrs} /></label>


    @app.page("/")
    def Home():
        n = 0
        on = False

        def inc():
            n += 1

        def flip():
            on = not on
        extra = {"data-on": "yes", "class": "lit"} if on else {"title": "off"}
        <main>
            <Button id="inc" onclick={inc} aria-label={"count " + str(n)} class_="mine">n={n}</Button>
            <Button id="flip" variant="outline" onclick={flip}>flip</Button>
            <div id="box" class="box" {**extra}>box</div>
            <Field label="Name" id="name" placeholder={"P" + str(n)} required={on} />
            <Badge id="badge" tone={"success" if on else None}>b</Badge>
        </main>
''')


def test_attributes_pass_through_in_the_browser(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(SPREAD)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        btn = page.locator("#inc")
        expect(btn).to_have_class("pw-button mine")
        page.click("#inc")
        expect(btn).to_have_text("n=1")                                   # the handler came through **attrs
        expect(btn).to_have_attribute("aria-label", "count 1")            # and its attributes follow state
        expect(page.locator("#name")).to_have_attribute("placeholder", "P1")
        box = page.locator("#box")
        expect(box).to_have_attribute("title", "off")
        expect(box).to_have_class("box")
        page.click("#flip")
        expect(box).to_have_class("box lit")                              # classes merge
        expect(box).to_have_attribute("data-on", "yes")
        assert box.get_attribute("title") is None                         # keys the dict dropped are removed
        expect(page.locator("#name")).to_have_attribute("required", "")
        expect(page.locator("#badge")).to_have_attribute("data-tone", "success")
        page.click("#flip")
        expect(box).to_have_class("box")
        assert box.get_attribute("data-on") is None


DIALOGS = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import (Button, Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle,
                          DialogDescription, DialogFooter, DialogClose, AlertDialog, AlertDialogTrigger,
                          AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter,
                          AlertDialogCancel, AlertDialogAction, Sheet, SheetTrigger, SheetContent, SheetTitle)

    app = App(title="Dialogs")


    @app.page("/")
    def Home():
        deleted = 0
        show = False
        changes = 0

        def delete():
            deleted += 1

        def toggle():
            show = not show

        def changed(e):
            changes += 1
        <main>
            <p id="deleted">{deleted}</p>
            <p id="changes">{changes}</p>
            <Dialog id="profile" onopenchange={changed}>
                <DialogTrigger id="open">Edit profile</DialogTrigger>
                <DialogContent id="panel">
                    <DialogHeader>
                        <DialogTitle>Edit profile</DialogTitle>
                        <DialogDescription>Make changes here.</DialogDescription>
                    </DialogHeader>
                    <input id="name" value="Ada" />
                    <DialogFooter><DialogClose id="done">Done</DialogClose></DialogFooter>
                </DialogContent>
            </Dialog>
            <AlertDialog>
                <AlertDialogTrigger id="del" variant="destructive">Delete</AlertDialogTrigger>
                <AlertDialogContent id="confirm">
                    <AlertDialogTitle>Delete it?</AlertDialogTitle>
                    <AlertDialogDescription>This can't be undone.</AlertDialogDescription>
                    <AlertDialogFooter>
                        <AlertDialogCancel id="cancel">Cancel</AlertDialogCancel>
                        <AlertDialogAction id="yes" variant="destructive" onclick={delete}>Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
            <Button id="toggle" onclick={toggle}>toggle</Button>
            <Sheet open={show}>
                <SheetContent id="sheet" side="left"><SheetTitle>Menu</SheetTitle></SheetContent>
            </Sheet>
        </main>
''')


def _ui_ready(page, selector):
    page.wait_for_selector(selector + "[data-pw-ui-ready]", state="attached")


def test_dialogs(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(DIALOGS)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#profile")
        panel = page.locator("#panel")
        trigger = page.locator("#open")
        expect(trigger).to_have_attribute("aria-expanded", "false")
        trigger.click()
        expect(panel).to_be_visible()
        expect(panel).to_have_attribute("role", "dialog")
        expect(panel).to_have_attribute("aria-modal", "true")
        title_id = page.locator("#panel [data-part=title]").get_attribute("id")
        expect(panel).to_have_attribute("aria-labelledby", title_id)
        expect(trigger).to_have_attribute("aria-expanded", "true")
        expect(page.locator("#name")).to_be_focused()                       # first focusable inside
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")                                          # wraps around (name, Done, x)
        expect(page.locator("#name")).to_be_focused()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()                                     # focus goes back
        expect(page.locator("#changes")).to_have_text("2")                  # openchange: open, then closed
        trigger.click()
        expect(panel).to_be_visible()
        page.mouse.click(5, 5)                                              # the backdrop
        expect(panel).to_be_hidden()
        trigger.click()
        page.click("#done")
        expect(panel).to_be_hidden()

        page.click("#del")
        confirm = page.locator("#confirm")
        expect(confirm).to_have_attribute("role", "alertdialog")
        expect(page.locator("#cancel")).to_be_focused()                     # the safe choice
        page.mouse.click(5, 5)
        expect(confirm).to_be_visible()                                     # no light dismiss
        page.click("#yes")
        expect(confirm).to_be_hidden()
        expect(page.locator("#deleted")).to_have_text("1")

        sheet = page.locator("#sheet")
        expect(sheet).to_be_hidden()
        page.click("#toggle")                                               # open={show}: controlled
        expect(sheet).to_be_visible()
        expect(sheet).to_have_attribute("data-side", "left")
        page.keyboard.press("Escape")
        expect(sheet).to_be_hidden()


FLOATING = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import Button, Popover, PopoverTrigger, PopoverContent, PopoverClose, Tooltip, HoverCard, HoverCardTrigger, HoverCardContent

    app = App(title="Floating")


    @app.page("/")
    def Home():
        <main style="padding: 120px">
            <Popover id="pop" side="bottom" align="start">
                <PopoverTrigger id="dims">Dimensions</PopoverTrigger>
                <PopoverContent id="panel" arrow={True}>
                    <label>Width <input id="w" value="100" /></label>
                    <PopoverClose id="x">Close</PopoverClose>
                </PopoverContent>
            </Popover>
            <Tooltip text="Copy to clipboard" id="tip"><Button id="copy" size="icon" aria-label="Copy">C</Button></Tooltip>
            <HoverCard id="hc"><HoverCardTrigger id="who" href="/ada">@ada</HoverCardTrigger><HoverCardContent id="card">Ada Lovelace</HoverCardContent></HoverCard>
            <button id="elsewhere">elsewhere</button>
        </main>
''')


def test_floating_panels(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(FLOATING)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#pop")
        panel, trigger = page.locator("#panel"), page.locator("#dims")
        expect(panel).to_be_hidden()
        trigger.click()
        expect(panel).to_be_visible()
        expect(page.locator("#w")).to_be_focused()
        expect(trigger).to_have_attribute("aria-expanded", "true")
        expect(panel).to_have_attribute("data-side", "bottom")
        t, p = trigger.bounding_box(), panel.bounding_box()
        assert abs(p["x"] - t["x"]) < 2 and p["y"] >= t["y"] + t["height"]       # below, aligned to the start
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()
        trigger.click()
        page.click("#elsewhere")                                                     # outside
        expect(panel).to_be_hidden()
        trigger.click()
        page.click("#x")
        expect(panel).to_be_hidden()

        _ui_ready(page, "#tip")
        tip = page.locator("#tip [data-part=content]")
        page.keyboard.press("Tab")                 # from the popover trigger to the button: focus shows it
        expect(page.locator("#copy")).to_be_focused()
        expect(tip).to_be_visible()
        expect(tip).to_have_attribute("role", "tooltip")
        expect(page.locator("#copy")).to_have_attribute("aria-describedby", tip.get_attribute("id"))
        b, tb = page.locator("#copy").bounding_box(), tip.bounding_box()
        assert tb["y"] + tb["height"] <= b["y"]                                      # above
        page.keyboard.press("Escape")
        expect(tip).to_be_hidden()
        page.hover("#copy")
        expect(tip).to_be_visible()
        page.mouse.move(5, 5)
        expect(tip).to_be_hidden()

        _ui_ready(page, "#hc")
        page.hover("#who")
        expect(page.locator("#card")).to_be_visible()
        page.mouse.move(5, 5)
        expect(page.locator("#card")).to_be_hidden()


MENUS = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import (DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, MenuItem, MenuCheckboxItem,
                          MenuRadioGroup, MenuRadioItem, MenuLabel, MenuSeparator, MenuSub, MenuSubTrigger,
                          MenuSubContent, ContextMenu, ContextMenuTrigger, ContextMenuContent, Menubar, MenubarMenu,
                          MenubarTrigger, MenubarContent)

    app = App(title="Menus")


    @app.page("/")
    def Home():
        last = ""
        compact = False

        def pick(name):
            last = name

        def toggle(e):
            compact = e.detail.checked
        <main style="padding: 40px">
            <p id="last">{last}</p>
            <p id="compact">{compact}</p>
            <DropdownMenu id="dd">
                <DropdownMenuTrigger id="opts">Options</DropdownMenuTrigger>
                <DropdownMenuContent id="menu">
                    <MenuLabel>Account</MenuLabel>
                    <MenuItem id="profile" onclick={lambda: pick("profile")} shortcut="P">Profile</MenuItem>
                    <MenuItem id="billing" onclick={lambda: pick("billing")}>Billing</MenuItem>
                    <MenuItem id="off" disabled={True}>Disabled</MenuItem>
                    <MenuCheckboxItem id="cb" checked={compact} oncheckedchange={toggle}>Compact</MenuCheckboxItem>
                    <MenuSeparator />
                    <MenuRadioGroup id="size">
                        <MenuRadioItem value="s" id="small" keep_open={True}>Small</MenuRadioItem>
                        <MenuRadioItem value="l" id="large" checked={True} keep_open={True}>Large</MenuRadioItem>
                    </MenuRadioGroup>
                    <MenuSub>
                        <MenuSubTrigger id="share">Share</MenuSubTrigger>
                        <MenuSubContent id="sharemenu">
                            <MenuItem id="email" onclick={lambda: pick("email")}>Email</MenuItem>
                        </MenuSubContent>
                    </MenuSub>
                </DropdownMenuContent>
            </DropdownMenu>
            <ContextMenu id="ctx">
                <ContextMenuTrigger id="area" style="width:200px;height:80px;border:1px dashed">Right-click</ContextMenuTrigger>
                <ContextMenuContent id="ctxmenu"><MenuItem id="copy" onclick={lambda: pick("copy")}>Copy</MenuItem></ContextMenuContent>
            </ContextMenu>
            <Menubar id="bar">
                <MenubarMenu><MenubarTrigger id="file">File</MenubarTrigger>
                    <MenubarContent id="filemenu"><MenuItem id="new" onclick={lambda: pick("new")}>New</MenuItem></MenubarContent>
                </MenubarMenu>
                <MenubarMenu><MenubarTrigger id="edit">Edit</MenubarTrigger>
                    <MenubarContent id="editmenu"><MenuItem id="undo" onclick={lambda: pick("undo")}>Undo</MenuItem></MenubarContent>
                </MenubarMenu>
            </Menubar>
        </main>
''')


def test_menus(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(MENUS)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#dd")
        menu, trigger = page.locator("#menu"), page.locator("#opts")
        trigger.focus()
        page.keyboard.press("ArrowDown")                                      # opens on the first item
        expect(menu).to_be_visible()
        expect(menu).to_have_attribute("role", "menu")
        expect(page.locator("#profile")).to_be_focused()
        expect(page.locator("#profile")).to_have_attribute("role", "menuitem")
        page.keyboard.press("ArrowDown")
        expect(page.locator("#billing")).to_be_focused()
        page.keyboard.press("ArrowDown")                                      # skips the disabled item
        expect(page.locator("#cb")).to_be_focused()
        page.keyboard.press("End")
        expect(page.locator("#share")).to_be_focused()
        page.keyboard.press("Home")
        expect(page.locator("#profile")).to_be_focused()
        page.keyboard.press("b")                                              # typeahead
        expect(page.locator("#billing")).to_be_focused()
        page.keyboard.press("Enter")
        expect(menu).to_be_hidden()
        expect(page.locator("#last")).to_have_text("billing")
        expect(trigger).to_be_focused()

        page.keyboard.press("ArrowUp")                                        # opens on the last item
        expect(page.locator("#share")).to_be_focused()
        page.keyboard.press("ArrowRight")                                     # submenu
        expect(page.locator("#sharemenu")).to_be_visible()
        expect(page.locator("#email")).to_be_focused()
        page.keyboard.press("ArrowLeft")
        expect(page.locator("#sharemenu")).to_be_hidden()
        expect(page.locator("#share")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        page.click("#cb")
        expect(page.locator("#compact")).to_have_text("True")
        trigger.click()
        expect(page.locator("#cb")).to_have_attribute("aria-checked", "true")
        page.click("#small")                                                   # keep_open: stays open
        expect(menu).to_be_visible()
        expect(page.locator("#small")).to_have_attribute("aria-checked", "true")
        expect(page.locator("#large")).to_have_attribute("aria-checked", "false")
        page.keyboard.press("Escape")

        _ui_ready(page, "#ctx")
        page.click("#area", button="right")
        expect(page.locator("#ctxmenu")).to_be_visible()
        page.click("#copy")
        expect(page.locator("#ctxmenu")).to_be_hidden()
        expect(page.locator("#last")).to_have_text("copy")

        _ui_ready(page, "#bar")
        page.locator("#file").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#filemenu")).to_be_visible()
        page.keyboard.press("ArrowRight")                                     # the next menu opens
        expect(page.locator("#filemenu")).to_be_hidden()
        expect(page.locator("#editmenu")).to_be_visible()
        expect(page.locator("#undo")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#last")).to_have_text("undo")


PICKERS = textwrap.dedent('''
    from pyweb import App, server
    from pyweb.ui import (Button, SelectMenu, SelectOption, Combobox, ComboboxOption, ComboboxEmpty, CommandDialog,
                          CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem, Toaster, toast)

    app = App(title="Pickers")


    @server
    def save() -> bool:
        toast("Saved", "Your changes are live.", tone="success")
        return True


    @app.page("/")
    def Home():
        size = "m"
        fruit = ""
        ran = ""

        def size_changed(e):
            size = e.detail.value

        def fruit_changed(e):
            fruit = e.detail.value

        def run(name):
            ran = name

        async def do_save():
            await save()
        <main style="padding: 40px">
            <p id="size">{size}</p><p id="fruit">{fruit}</p><p id="ran">{ran}</p>
            <form id="f"><SelectMenu id="sel" name="size" value="m" onvaluechange={size_changed}>
                <SelectOption value="s">Small</SelectOption>
                <SelectOption value="m">Medium</SelectOption>
                <SelectOption value="l">Large</SelectOption>
            </SelectMenu></form>
            <Combobox id="cb" onvaluechange={fruit_changed}>
                <ComboboxOption value="apple">Apple</ComboboxOption>
                <ComboboxOption value="banana">Banana</ComboboxOption>
                <ComboboxOption value="cherry">Cherry</ComboboxOption>
                <ComboboxEmpty>No fruit.</ComboboxEmpty>
            </Combobox>
            <CommandDialog id="cmd" shortcut="k">
                <CommandInput />
                <CommandList>
                    <CommandEmpty>Nothing found.</CommandEmpty>
                    <CommandGroup heading="Pages">
                        <CommandItem id="go-home" onclick={lambda: run("home")}>Home</CommandItem>
                        <CommandItem id="go-settings" keywords="preferences" onclick={lambda: run("settings")}>Settings</CommandItem>
                    </CommandGroup>
                </CommandList>
            </CommandDialog>
            <Button id="save" onclick={do_save}>Save</Button>
            <Toaster />
        </main>
''')


def test_pickers_and_toasts(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(PICKERS)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        _ui_ready(page, "#sel")
        trig = page.locator("#sel [data-part=trigger]")
        expect(trig).to_have_text("Medium")
        expect(trig).to_have_attribute("role", "combobox")
        trig.focus()
        page.keyboard.press("ArrowDown")                                      # opens on the selected option
        listbox = page.locator("#sel [data-part=content]")
        expect(listbox).to_be_visible()
        expect(trig).to_have_attribute("aria-activedescendant", page.locator("#sel [data-value=m]").get_attribute("id"))
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(listbox).to_be_hidden()
        expect(trig).to_have_text("Large")
        expect(page.locator("#size")).to_have_text("l")
        assert page.locator("#f input[name=size]").input_value() == "l"       # forms get the value
        page.keyboard.press("s")                                              # typeahead while closed
        expect(trig).to_have_text("Small")

        _ui_ready(page, "#cb")
        inp = page.locator("#cb [data-part=input]")
        inp.fill("an")
        opts = page.locator("#cb [data-part=option]")
        expect(page.locator("#cb [data-value=banana]")).to_be_visible()
        expect(page.locator("#cb [data-value=apple]")).to_be_hidden()
        page.keyboard.press("Enter")
        expect(inp).to_have_value("Banana")
        expect(page.locator("#fruit")).to_have_text("banana")
        inp.fill("zzz")
        expect(page.locator("#cb [data-part=empty]")).to_be_visible()
        assert opts.count() == 3

        _ui_ready(page, "#cmd")
        page.keyboard.press("Control+k")
        search = page.locator("#cmd [data-part=input]")
        expect(search).to_be_visible()
        expect(search).to_be_focused()
        search.fill("prefs")                                                   # matches keywords
        expect(page.locator("#go-settings")).to_be_visible()
        expect(page.locator("#go-home")).to_be_hidden()
        page.keyboard.press("Enter")
        expect(page.locator("#ran")).to_have_text("settings")
        expect(search).to_be_hidden()

        page.click("#save")                                                    # toast() in a server function
        t = page.locator(".pw-toast")
        expect(t).to_have_count(1)
        expect(t).to_contain_text("Saved")
        expect(t).to_have_attribute("role", "status")
        page.click(".pw-toast [data-part=close]")
        expect(t).to_have_count(0)
