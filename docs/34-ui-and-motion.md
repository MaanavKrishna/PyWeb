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

Pages that use the kit get its stylesheet (`/__pyweb/ui.css`, about
3 KB gzipped) and the theme (`/__pyweb/theme.css`); pages that don't
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

Each component's behaviour is a small module (1-2.5 KB gzipped) that
loads only on pages that use that component.

## Customising

Each level is optional; most apps stop at the first.

1. **The theme.** `Theme(...)`, a preset, or CSS variables.
2. **Props.** `variant=`, `size=` and `tone=` on components. Any other
   attribute (`id`, `aria-*`, `data-*`, handlers) reaches the element,
   and `class_=` adds your classes to the kit's.
3. **State and part attributes.** Every element marks its part and
   state (`data-part="title"`, `data-variant="outline"`,
   `data-state="open"`), so CSS can style any part in any state:

   ```css
   .pw-button[data-variant="outline"] { border-width: 2px; }
   .pw-card [data-part="title"] { font-family: "Fraunces", serif; }
   ```

   The kit's CSS lives in the `pyweb` cascade layer, so any CSS you
   write wins, however specific its selectors.
4. **Own the code.** `pyweb ui add button` copies the component's
   `.pyweb` file into `components/`. Change it as you like and import it
   from there (`from components.button import Button`). `pyweb ui diff
   button` shows how your copy differs from the kit after an upgrade;
   `pyweb ui list` lists everything in the kit.
