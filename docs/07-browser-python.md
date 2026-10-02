# Python in the browser

Event handlers, markup expressions, computed values and helpers that
browser code calls are compiled from Python to JavaScript. PyWeb
compiles a **defined subset** of Python, with Python's semantics where
JavaScript differs, and reports anything outside it as a compile error
with the file and line.

The translation is checked continuously by a differential test that
evaluates well over a hundred expressions in CPython and in the
compiled JavaScript and requires identical results, including which
exception is raised.

## Supported

**Expressions:** literals (int, float, str, bool, None, list, tuple,
dict, set), f-strings (with format specs such as `{x:.2f}`, `{n:,}`,
`{v!r}`), arithmetic (`+ - * / // % **`, with Python's floor division
and modulo signs), comparisons including chained ones (`0 < x < 10`),
`==` on lists and dicts (structural), `in`/`not in`, `is None`,
`and`/`or`/`not` with Python truthiness (empty list, dict and string are
false), conditional expressions, indexing with negative indices,
slicing with steps, list/dict/set comprehensions and generator
expressions (multiple `for`/`if` clauses), lambdas, unpacking in
literals (`[*a, *b]`, `{**d}`), `await`.

**Statements:** assignment (including tuple unpacking and chained
assignment), augmented assignment, `if`/`elif`/`else`, `for` (with
tuple targets), `while`, `break`, `continue`, `return`, `pass`,
`del x[k]`, `try`/`except`/`finally` (matching on exception class
names, `except X as e`), `raise`, `assert`, nested `def`.

**Builtins:** `len`, `str`, `repr`, `int`, `float`, `bool`, `abs`,
`min`, `max` (with `key=`, `default=`), `sum`, `round` (banker's
rounding like Python), `range`, `sorted` (`key=`, `reverse=`),
`reversed`, `enumerate`, `zip`, `list`, `tuple`, `dict`, `set`, `any`,
`all`, `chr`, `ord`, `isinstance` (with builtin type names), `print`
(to the browser console).

**Methods:**

- `str`: `upper lower strip lstrip rstrip split splitlines join replace
  startswith endswith find rfind index count title capitalize isdigit
  isnumeric isalpha isalnum isspace isupper islower zfill ljust rjust
  center format`
- `list`: `append extend insert pop remove index count clear sort
  reverse copy`
- `dict`: `get keys values items pop update setdefault copy clear`
- `set`: `add discard remove clear copy`

**Errors:** out-of-range indexes raise `IndexError`, missing keys
`KeyError`, bad conversions `ValueError`, mixing `str + int`
`TypeError`, division by zero `ZeroDivisionError`, so the same
`try/except` you would write in Python works.

## Not supported (compile errors)

Classes, `with`, `import` inside functions, generators (`yield`),
`global` state outside the page, the walrus operator, `*args`/`**kwargs`
parameters, slice assignment, `for`/`else` and `while`/`else`, keyword
arguments to arbitrary JavaScript functions, integers larger than
2**53, and any module-level name that only exists on the server
(imports, database handles, classes). For those, call an `@server`
function.

## Known differences

- JavaScript has one number type: `2.0` prints as `2` and `1/3` uses
  float arithmetic as in Python, but `int` and `float` are not
  distinguished when printing whole numbers.
- `dict` values in the browser are plain JSON objects, so keys are
  strings. Attribute access (`user.name`) and subscripting
  (`user["name"]`) both work on them.
- `print` writes to the browser console.
- Strings compare by UTF-16 code units, which only differs from Python
  for characters outside the Basic Multilingual Plane.

## Seeing the output

The compiled module for each page is readable JavaScript (minified only
by `pyweb build --production`). In `pyweb dev` open
`/static/<PageName>.js` to see exactly what runs in the browser.
