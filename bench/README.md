# Benchmark workloads

Fixed copies of the todo example and the saas template, as they were in 0.5. `python -m pyweb.bench --system`
measures these rather than the current examples, so redesigning an example doesn't read as a speed change
in CI's comparison with the base commit. Change them only on purpose, in a commit of their own.
