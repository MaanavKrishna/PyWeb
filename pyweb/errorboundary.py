"""``<ErrorBoundary fallback="...">``: keep one broken part from taking the whole page down.

::

    from pyweb import ErrorBoundary

    <ErrorBoundary fallback="The chart couldn't load.">
        <Chart data={rows} />
    </ErrorBoundary>

If rendering the children raises on the server, the page is sent with the fallback in their place
(and the error reported like any page error). In the browser, if code inside the boundary throws
while handling an event, that part is swapped for the fallback with a Reload button, and the rest of
the page keeps working. Not found and bad request errors still answer for the whole page.
"""


class ErrorBoundary:
    """A marker the compiler replaces (see the module docs); it isn't called at run time."""

    def __init__(self, *args, **kwargs):
        raise TypeError("<ErrorBoundary> is markup: use it in a page or component, not as a function")
