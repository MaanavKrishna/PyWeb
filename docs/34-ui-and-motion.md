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
