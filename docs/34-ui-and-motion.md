# UI kit and motion

PyWeb comes with a component kit: accessible, themed, server-rendered
components in the spirit of shadcn/ui and Radix. It also comes with a
motion system in the spirit of motion.dev. Both are plain `.pyweb`
code and CSS variables, so you can change them at every level, from
one line of theme to owning a component's source.

```pyweb
from pyweb import App
from pyweb.ui import Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Theme

app = App(title="Billing", theme=Theme(accent="#6366f1", radius=10))


@app.page("/")
def Billing():
    <Card>
        <CardHeader>
            <CardTitle>Pro plan</CardTitle>
            <CardDescription>Renews on 1 November</CardDescription>
        </CardHeader>
        <CardContent>
            <Badge tone="success">Active</Badge>
            <Button variant="outline" href="/plans">Change plan</Button>
        </CardContent>
    </Card>
```

**See it:** the UI kit and motion example (`examples/ui/app.pyweb`,
also in the playground and as a live demo on the website) tours the
components and the animations. The `pyweb new` templates use the kit
too: `todo` animates its rows, and `saas` has a sidebar layout, an
account menu, a dialog, toasts and animated live tasks. The sign-in,
account and `/admin` pages are built from the same classes and follow
your theme.

**With an AI assistant:** the `pyweb_ui` MCP tool lists every component
with its variants and sizes, shows one component's parameters and source
(`pyweb://kit/<Component>`), copies one into your app and makes a
theme; `pyweb_review` screenshots a page in light and dark at desktop and
phone widths with an accessibility audit; the `design_pass` prompt puts
them together.

Pages that use the kit get its stylesheet (`/__pyweb/ui.css`, about
9 KB gzipped) and the theme (`/__pyweb/theme.css`); pages that don't
use it get neither.

## The theme

`App(theme=Theme(...))` sets the look of every kit component and of
the auth pages:

| Setting | Default | |
|---|---|---|
| `accent` | `"#6366f1"` | your brand colour |
| `gray` | tinted toward the accent | the neutral's hue |
| `radius` | `10` | corner radius, in px |
| `font` | `"system"` | `"system"`, `"inter"`, `"serif"`, `"mono"` or any CSS font-family |
| `density` | `"comfortable"` | `"compact"`, `"comfortable"` or `"spacious"` |
| `dark` | `"auto"` | follow the OS, or always `"light"`/`"dark"` |

`Theme.preset("zinc")` (or `App(theme="zinc")`) picks a built-in look:
indigo, zinc, slate, rose, ocean, violet, emerald or amber.
`Theme.preset("rose", radius=4)` changes one value of a preset.

From one accent, PyWeb computes 12-step colour scales (as Radix Colors
does) for the accent, a matching gray and the status colours (danger,
success, warning, info), in light and dark. It works in OKLCH, so the
steps look evenly spaced. Every scale is checked for contrast: text
steps and text on solid fills reach 4.5:1, and are adjusted when a
colour falls short.

| Steps | Use |
|---|---|
| 1-2 | app and subtle backgrounds |
| 3-5 | component backgrounds: normal, hover, pressed |
| 6-8 | borders and focus rings |
| 9-10 | solid fills (buttons): normal, hover |
| 11-12 | coloured and high-contrast text |

Everything is a CSS variable, so plain CSS can change any of it:
`--pw-accent-1` ... `--pw-accent-12`, `--pw-gray-*`, `--pw-danger-*`,
`--pw-bg`, `--pw-fg`, `--pw-fg-muted`, `--pw-border`, `--pw-ring`,
`--pw-radius`, `--pw-font`, `--pw-space-1` ... `--pw-space-12`,
`--pw-shadow-1` ... `--pw-shadow-3`. Setting `data-theme="dark"` (or
`"light"`) on `<html>` overrides the OS setting.

## Components

| Import | |
|---|---|
| `Button`, `ButtonGroup` | `variant`: default, secondary, outline, ghost, link, destructive. `size`: sm, md, lg, icon. `href=` makes a link that looks like a button; `loading=` shows a spinner and disables it |
| `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` | |
| `Badge` | `variant`: soft, solid, outline, secondary. `tone`: danger, success, warning |
| `Alert`, `AlertTitle`, `AlertDescription` | `tone`: danger, success, warning, info (danger and warning are announced to screen readers) |
| `Separator` | `orientation`, `decorative` |
| `Skeleton` | `width`, `height`, `radius` |
| `Kbd`, `Label`, `Avatar` (`src`, `alt`, `fallback`, `size`), `AspectRatio` (`ratio`), `Progress` (`value`, `max`, `label`) | |

The form tags (`<Form>`, `<Input>`, `<Textarea>`, `<Select>`,
`<Checkbox>`, `<Submit>`, from `pyweb`) are styled by the kit too.

### Overlays

```pyweb
from pyweb import App
from pyweb.ui import (Button, Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter,
                      DialogHeader, DialogTitle, DialogTrigger)

app = App(title="Profile")


@app.page("/")
def Profile():
    <Dialog>
        <DialogTrigger>Edit profile</DialogTrigger>
        <DialogContent>
            <DialogHeader>
                <DialogTitle>Edit profile</DialogTitle>
                <DialogDescription>Changes are saved when you press Done.</DialogDescription>
            </DialogHeader>
            <input value="Ada" aria-label="Name" />
            <DialogFooter><DialogClose>Done</DialogClose></DialogFooter>
        </DialogContent>
    </Dialog>
```

| Component | Parts |
|---|---|
| `Dialog` | `DialogTrigger`, `DialogContent` (`size`: sm, md, lg, full), `DialogHeader`, `DialogTitle`, `DialogDescription`, `DialogFooter`, `DialogClose` |
| `AlertDialog` | `AlertDialogTrigger`, `AlertDialogContent`, `AlertDialogTitle`, `AlertDialogDescription`, `AlertDialogFooter`, `AlertDialogCancel`, `AlertDialogAction` |
| `Sheet` | `SheetTrigger`, `SheetContent` (`side`: right, left, top, bottom), `SheetHeader`, `SheetTitle`, `SheetDescription`, `SheetFooter`, `SheetClose` |
| `Popover` (`side`, `align`, `offset`) | `PopoverTrigger`, `PopoverContent` (`arrow`), `PopoverClose` |
| `Tooltip` (`text`, `side`, `delay`) | wraps the element it describes; or `TooltipTrigger`, `TooltipContent` |
| `HoverCard` | `HoverCardTrigger` (a link), `HoverCardContent` |
| `DropdownMenu` (`side`, `align`) | `DropdownMenuTrigger`, `DropdownMenuContent` |
| `ContextMenu` | `ContextMenuTrigger` (the area to right-click), `ContextMenuContent` |
| `Menubar` | `MenubarMenu`, `MenubarTrigger`, `MenubarContent` |
| menu items (all three menus) | `MenuItem` (`shortcut`, `disabled`, `keep_open`), `MenuCheckboxItem` (`checked`), `MenuRadioGroup`, `MenuRadioItem` (`value`), `MenuLabel`, `MenuSeparator`, `MenuSub`, `MenuSubTrigger`, `MenuSubContent` |

They follow the WAI-ARIA Authoring Practices for each pattern:
- Dialogs keep focus inside, close on Escape or a click outside
  (alert dialogs don't: they focus Cancel), and give focus back to what
  opened them. They're built on the native `<dialog>`, so the rest of
  the page is inert while one is open.
- Menus open with Enter, Space or the arrow keys. Arrows, Home/End and
  typing the first letters move. Enter chooses, ArrowRight and
  ArrowLeft open and close submenus, and Escape or Tab close.
- Popovers, menus and tooltips flip to the other side when there isn't
  room, stay inside the window, and size themselves to the space
  available.

Every `*Trigger` is a button that takes `variant`/`size`; any element
with `data-part="trigger"` inside the root works too (`<Button
data-part="trigger">`).

**Controlled and events.** `open={flag}` on `Dialog`, `AlertDialog`,
`Sheet` and `Popover` opens and closes them when `flag` changes. Every
root fires `openchange` (`onopenchange={handler}`, with
`event.detail.open`). Checkbox items fire `checkedchange`
(`event.detail.checked`); radio groups and pickers fire `valuechange`
(`event.detail.value`).

### Pickers

| Component | |
|---|---|
| `SelectMenu` (`name`, `value`, `placeholder`) + `SelectOption` (`value`, `label`, `disabled`) | a styled select. Typing picks options, and it works in forms through `name` |
| `Combobox` (`name`, `value`, `placeholder`, `filter`) + `ComboboxOption`, `ComboboxEmpty` | type to filter (`filter="server"` when your `oninput` renders new options) |
| `Command` / `CommandDialog` (`shortcut`, default ⌘K / Ctrl+K) + `CommandInput`, `CommandList`, `CommandEmpty`, `CommandGroup` (`heading`), `CommandItem` (`keywords`, `shortcut`, `onclick`), `CommandSeparator` | a command palette: results are ranked as you type (fuzzy, with `keywords`), and Enter or a click runs one |

### Toasts

```python
from pyweb import server
from pyweb.ui import toast


@server
def save(text: str) -> None:
    ...
    toast("Saved", "Your changes are live.", tone="success")
```

`toast(title, description=None, tone=None, duration=5000, action=None)`
works in server functions, form actions and pages; the page that gets
the answer shows it (after a form's redirect too). `tone` is success,
danger, warning or info; `duration=0` keeps it until dismissed;
`action={"label": "Undo", "href": "/undo/42"}` adds a button. Put a
`<Toaster position="bottom-right" />` in your layout to choose where
toasts appear (it's added for you otherwise). Toasts stack and spread
out on hover, pause while hovered or focused, can be swiped away, and
are announced to screen readers (danger ones at once). Browser code can
call `pywebUI.toast({"title": "Copied"})`.

Each component's behaviour is a small module (0.3-3 KB gzipped) that
loads only on pages that use that component.

### Tabs, accordions and collapsibles

```html
<Tabs default="account">
    <TabsList>
        <TabsTrigger value="account">Account</TabsTrigger>
        <TabsTrigger value="password">Password</TabsTrigger>
    </TabsList>
    <TabsContent value="account">...</TabsContent>
    <TabsContent value="password">...</TabsContent>
</Tabs>

<Accordion type="single" default="shipping">
    <AccordionItem value="shipping">
        <AccordionTrigger>How long does shipping take?</AccordionTrigger>
        <AccordionContent>Two to four days.</AccordionContent>
    </AccordionItem>
</Accordion>
```

| Component | |
|---|---|
| `Tabs` (`default`, `value`, `orientation`, `activation`) + `TabsList`, `TabsTrigger` (`value`, `disabled`), `TabsContent` (`value`) | arrow keys move between tabs and show them (`activation="manual"`: Enter shows), Home/End jump; the underline slides to the chosen tab. Put the default tab first: it shows until scripts load |
| `Accordion` (`type`: single or multiple, `default`, `value`, `collapsible`) + `AccordionItem` (`value`, `open`, `disabled`), `AccordionTrigger`, `AccordionContent` | native `<details>`: works without scripts, and find-in-page opens a closed section. Height animates; arrows move between headers |
| `Collapsible` (`open`) + `CollapsibleTrigger`, `CollapsibleContent` | one section that opens and closes |

`value=` makes `Tabs` and `Accordion` controlled: they fire
`valuechange` and show what your state says.

### Form controls

Native inputs underneath (forms, validation, keyboard and screen
readers come from the browser), styled by the kit. With `name=` they're
sent with any form.

| Component | |
|---|---|
| `Checkbox` (`label`, `checked`, `indeterminate`, `name`, `value`) | `indeterminate=` shows the mixed state |
| `Switch` (`label`, `checked`, `name`, `size`) | a checkbox with `role="switch"` |
| `RadioGroup` (`name`, `value`, `options`, `orientation`) | `options=[("free", "Free"), ...]`, or `RadioGroupItem` (`value`, `label`) children |
| `Toggle` (`pressed`, `default`, `variant`, `label`) | a button that stays pressed; fires `pressedchange` |
| `ToggleGroup` (`type`: single or multiple, `value`, `default`, `name`) + `ToggleGroupItem` (`value`, `label`) | single works like radio buttons; fires `valuechange` |
| `Slider` (`value`, `min`, `max`, `step`, `name`, `label`, `show_value`) | a range input with a filled track |
| `InputOTP` (`length`, `name`, `pattern`: digits or alphanumeric, `group`) | one real `autocomplete="one-time-code"` input drawn as boxes (paste and SMS autofill work); fires `complete` |

### Layout and navigation

```html
<SidebarProvider>
    <Sidebar>
        <SidebarHeader>Acme</SidebarHeader>
        <SidebarContent>
            <SidebarGroup label="Workspace">
                <SidebarMenu>
                    <SidebarMenuItem>
                        <SidebarMenuButton href="/" active={True} icon="⌂">Home</SidebarMenuButton>
                    </SidebarMenuItem>
                </SidebarMenu>
            </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>...</SidebarFooter>
    </Sidebar>
    <SidebarInset>
        <SidebarTrigger />
        ...the page...
    </SidebarInset>
</SidebarProvider>
```

| Component | |
|---|---|
| `SidebarProvider` (`collapsed`, `collapsible`: icon, offcanvas or none, `side`, `variant`: sidebar or floating, `shortcut`) + `Sidebar`, `SidebarHeader`, `SidebarContent`, `SidebarFooter`, `SidebarGroup` (`label`), `SidebarMenu`, `SidebarMenuItem`, `SidebarMenuButton` (`href`, `active`, `icon`, `badge`), `SidebarSeparator`, `SidebarTrigger`, `SidebarRail`, `SidebarInset` | `SidebarTrigger` or Ctrl/⌘+B collapses it to icons (remembered in the browser). On small screens it opens over the page instead |
| `NavigationMenu` + `NavigationMenuList`, `NavigationMenuItem`, `NavigationMenuTrigger`, `NavigationMenuContent`, `NavigationMenuLink` (`href`, `description`, `active`) | a site menu: panels open on hover or click, arrows move along the bar, ArrowDown enters a panel, Escape closes |
| `Breadcrumb` + `BreadcrumbItem`, `BreadcrumbLink`, `BreadcrumbPage`, `BreadcrumbSeparator`, `BreadcrumbEllipsis` | |
| `Pagination` (`page`, `pages`, `href` or `onchange`, `siblings`) | links (`href="?page="` gets the number added) or buttons that call `onchange(n)`, with ellipses for long ranges |
| `ResizablePanelGroup` (`direction`, `save`) + `ResizablePanel` (`size`, `min`, `max`, `collapsible`), `ResizableHandle` (`grip`) | drag or use the arrow keys on a handle (sizes are percentages); `save="name"` remembers them |
| `ScrollArea` (`height`, `max_height`, `orientation`) | a scrolling region with thin scrollbars, reachable by keyboard |

### Dates

```html
<Calendar name="due" value="2026-11-02" />
<DatePicker name="trip" mode="range" placeholder="Pick dates" onvaluechange={changed} />
```

`Calendar` and `DatePicker` take `value` (an ISO date; a range is
`"start/end"`), `name`, `mode` (single or range), `min`, `max`,
`week_start` (0 Sunday, 1 Monday; the locale decides otherwise) and
`locale`. Arrow keys move a day or a week, Home/End go to the week's
ends, PageUp/PageDown change the month (with Shift, the year), and
Enter picks. Without scripts a `Calendar` is a native date field.

### Carousel

```html
<Carousel loop={True} label="Featured">
    <CarouselContent>
        for p in products:
            <CarouselItem basis="33%"><ProductCard product={p} /></CarouselItem>
    </CarouselContent>
    <CarouselPrevious />
    <CarouselNext />
    <CarouselDots />
</Carousel>
```

Slides sit on a scroll-snap track, so swipes and trackpads work as
they do anywhere else. Buttons, dots and the arrow keys move one slide;
`autoplay=5000` advances on a timer, paused while hovered or focused and
never with reduced motion. It fires `slidechange`.

### Data tables

```html
<DataTable rows={tasks} page_size={10} selectable={True} onselect={picked} columns={[
    {"key": "title", "label": "Task"},
    {"key": "points", "label": "Points", "align": "end"},
    {"key": "done", "label": "Done", "format": lambda v: "✓" if v else "", "sortable": False},
]} />
```

Search, sorting (click a header: ascending, descending, off), pages
and row selection all run in the browser; the first page is rendered on
the server. `rows` is a list of dicts. A live query works: the table
follows it. `onselect(keys)` gets the `key` (default `"id"`) of every
selected row. For a plain styled table there are `Table` (`caption`),
`TableHeader`, `TableBody`, `TableFooter`, `TableRow` (`selected`),
`TableHead` and `TableCell` (`align`).

### Charts

```html
<BarChart data={sales} x="month" y={["online", "store"]} label="Sales by month" />
<LineChart data={visits} x="day" y="visits" />
<AreaChart data={visits} x="day" y="visits" />
<PieChart data={plans} name="plan" value="users" donut={True} center="1,204" />
<Sparkline values={[3, 5, 2, 8, 6]} />
```

Charts are SVG drawn on the server: no chart library and nothing to
load. They redraw when their data changes (state or a live query).
`BarChart` takes `stacked=`, `LineChart` takes `area=` and `dots=`,
and each takes `height`, `colors`, `format` (a function for the
numbers on the axis and in tooltips) and `legend`. Colours come from
`--pw-chart-1` ... `--pw-chart-5` (accent, info, success, warning,
danger). Each chart has a label and a hidden table of its numbers for
screen readers, and every bar, point and slice has a tooltip.

`from pyweb.ui import *` imports every component; pages still only get
the components they use.

## Customising

Each level is optional; most apps stop at the first.

1. **The theme.** `Theme(...)`, a preset, or CSS variables. In `pyweb
   dev`, the toolbar's **Theme** tab changes the accent, gray, radius,
   font, density and dark mode on the running app as you drag. "Copy as
   code" gives the `Theme(...)` line; "Save to app.pyweb" writes it into
   your `App(...)` call. Nothing else in the file changes.
2. **Props.** `variant=`, `size=` and `tone=` on components (the editor
   completes the values and warns about unknown ones). Any other
   attribute (`id`, `aria-*`, `data-*`, handlers) reaches the element,
   and `class_=` adds your classes to the kit's.
3. **Your own variants of kit components.** `ui.extend` adds them, as
   CSS in the theme stylesheet:

   ```python
   from pyweb import ui

   ui.extend("Button", variant={
       "brand": {"background": "var(--pw-accent-11)", "color": "white",
                 "hover": {"background": "var(--pw-accent-12)"}},
   }, size={"xl": {"height": "52px", "padding": "0 28px"}})
   ```

   Then `<Button variant="brand" size="xl">` works, and the editor
   completes it. Sub-dicts named `hover`, `focus`, `active`, `disabled`,
   `open` or `checked` style that state. `Theme(extend={"Button":
   {"variant": {...}}})` does the same from the theme.
4. **Variants for your own components** (like cva, and tailwind-merge's
   core): `variants()` describes the classes, `style()` picks them, and
   `cx()` joins classes, the last of a conflicting group winning. They
   work in server code and browser code alike:

   ```python
   from pyweb.ui import cx, variants, style

   card = variants(
       base="rounded-lg border p-4",
       variant={"plain": "bg-white", "brand": "bg-indigo-600 text-white"},
       size={"sm": "p-2 text-sm", "lg": "p-6"},
       default={"variant": "plain"},
       compound=[{"variant": "brand", "size": "lg", "class": "shadow-lg"}],
   )
   ```

   In the component, `class={style(card, variant=variant, size=size,
   class_=class_)}` gives the classes, and the caller's `class_` wins
   conflicts (`p-10` replaces `p-4`). Options are `variant`, `size`,
   `tone` and `shape`.
5. **State and part attributes.** Every element marks its part and
   state (`data-part="title"`, `data-variant="outline"`,
   `data-state="open"`), so CSS can style any part in any state:

   ```css
   .pw-button[data-variant="outline"] { border-width: 2px; }
   .pw-card [data-part="title"] { font-family: "Fraunces", serif; }
   ```

   The kit's CSS lives in the `pyweb` cascade layer, so any CSS you
   write wins, however specific its selectors.
6. **Own the code.** `pyweb ui add button` copies the component's
   `.pyweb` file into `components/`. Change it as you like and import it
   from there (`from components.button import Button`). `pyweb ui diff
   button` shows how your copy differs from the kit after an upgrade;
   `pyweb ui list` lists everything in the kit.

Motion follows the same ladder:
1. **Global:** `App(motion=Motion(...))`.
2. **Named:** presets, including your own (`pyweb.motion.preset`).
3. **Per element:** `transition={{...}}`.
4. **Visual tuning:** in `pyweb dev`, the toolbar's **Motion** tab picks
   any animated element and changes its spring or duration with
   sliders. Each change replays the animation. "Copy as code" gives the
   `transition={{...}}`.

Plain functions in other `.pyweb` files can be imported into browser
code too (`from formatting import money`), along with the functions and
constants they use.

### Accessibility checks

`pyweb check` and the editor point out:
- images without `alt`;
- clicks on elements a keyboard can't reach (use `<Button>`);
- form fields without a label;
- buttons and links with no text;
- a positive `tabindex`;
- an empty `App(lang=...)`.

They're warnings; `pyweb check --strict` fails on them. The kit,
examples and templates pass.

## Motion

Animations are attributes on any element:

```pyweb
from pyweb import App

app = App(title="Tasks")


@app.page("/")
def Tasks():
    tasks = ["Write docs", "Ship 0.6"]

    def add():
        tasks = tasks + [f"Task {len(tasks) + 1}"]
    <main>
        <button onclick={add} while_hover={{"scale": 1.05}} while_tap={{"scale": 0.95}}>Add</button>
        <ul>
            for t in tasks:
                <li initial={{"opacity": 0, "y": 12}} animate={{"opacity": 1, "y": 0}}
                    exit={{"opacity": 0, "x": -40}} layout>{t}</li>
        </ul>
    </main>
```

A new row slides in, a removed one slides out before it leaves the page,
and the others glide to their new places.

| Attribute | |
|---|---|
| `initial` | the pose it starts in (rendered by the server, so the first paint already starts there). `initial={False}` starts at `animate` |
| `animate` | the pose to animate to. When the value changes (`animate={{"x": x}}`), it animates there from wherever it is |
| `exit` | the pose to animate to before it's removed (an `if` turning false, a row leaving a `for`) |
| `transition` | how: `{"type": "spring", "stiffness": 300, "damping": 24}`, `{"type": "spring", "duration": 0.4, "bounce": 0.3}`, `{"type": "tween", "duration": 0.3, "ease": "ease-out"}`, plus `delay`, `repeat` (a number, or `"infinite"`), `repeat_type` ("loop", "reverse"), and per value (`{"opacity": {"duration": 0.2}}`) |
| `while_hover`, `while_tap`, `while_focus`, `while_in_view` | poses held while the pointer is over it, while pressed (Enter and Space too), while keyboard-focused, while on screen (`viewport={{"once": True, "amount": 0.3}}`) |
| `variants` | named poses: `initial="hidden" animate="show"`. Children with `variants` follow their parent's label; the parent's `transition={{"stagger_children": 0.05}}` (and `delay_children`) staggers them |
| `layout` | animates every change in its position and size (a reorder, an element opening above it). `layout="position"` animates only position |
| `layout_id` | an element that appears with the same id as one that just went starts where that one was (a tab underline sliding between tabs). Across pages, client navigation morphs them |
| `drag` | `drag`, `drag="x"` or `drag="y"`, with `drag_constraints` (`{"left": -100, "right": 100}` or `"parent"`), `drag_elastic` (0-1), `drag_momentum` (default on) and `drag_snap_back`. It fires `dragstart`, `drag` and `dragend` |

Values are `x`, `y` (px, or a string like `"100%"`), `scale`, `scale_x`,
`scale_y`, `rotate`, `rotate_x`, `rotate_y` (degrees), `opacity`, `blur`,
and any CSS property (`"background_color": "#ff0"`, `"height": "auto"`).
A list is keyframes: `animate={{"x": [0, -8, 8, 0]}}`. Physical values
(position, scale, rotation) move on springs by default, and others on
tweens. Interrupting an animation keeps its momentum: the next one
starts from where the element is, at the speed it was going.

**Presets.** `animate="fade-up"` uses a built-in animation, which sets
the initial, animate and exit poses: `fade`, `fade-up`, `fade-down`,
`fade-left`, `fade-right`, `slide-up`, `slide-down`, `slide-left`,
`slide-right`, `scale`, `pop`, `blur-in`, `flip`, `collapse`, `shake`,
`pulse`. `animate="count"` makes a number count from its old value to
its new one whenever it changes (`<span animate="count">{total}</span>`).
Add your own with `pyweb.motion.preset("hero-in", {"initial": {...},
"animate": {...}})`.

**Components.** Motion attributes on a component reach its root element,
so `<Card animate="fade-up">` works with every kit component.
`motion={False}` turns a kit component's own animations off.

**Page transitions.** Client navigation runs inside the browser's View
Transitions where available (a cross-fade by default; style it with
`::view-transition-*`). Elements with the same `layout_id` on both pages
morph into each other.

**The whole app at once.** `App(motion=Motion(feel="gentle", speed=0.8,
reduced="auto"))` (`from pyweb.motion import Motion`) changes every
animation, the kit's included:
- `feel` is "snappy" (default), "gentle", "bouncy", or "none" (no
  animation);
- `speed` scales every duration;
- `reduced="auto"` follows the visitor's reduced-motion setting, so
  movement jumps and fades stay short. `"always"` reduces for everyone,
  and `"never"` ignores the setting.

**Browser code.** `from pyweb.motion import animate, timeline, stagger,
scroll_progress, use_transform` (in event handlers and other browser
code):

```python
animate(".row", {"x": [0, 12, 0]}, {"delay": stagger(0.05)})
timeline([["#title", {"opacity": 1}], ["#body", {"y": 0}, {"at": 0.2}]])
progress = scroll_progress()          # call progress() to read 0..1
width = use_transform(progress, [0, 1], [0, 100])
```

Without scripts, every animated element shows in its final pose.
Animations change only `opacity`, `translate`, `scale`, `rotate`,
`filter` and `transform` where they can, which the browser runs off the
main thread. The engine is about 7.5 KB gzipped (layout, drag and
counting load only on pages that use them), and pages without motion
don't load it.
