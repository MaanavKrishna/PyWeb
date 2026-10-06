"""Deployment: read the app, decide a production topology, write files for any target.

::

    pyweb deploy fly            # or docker | compose | k8s | render | railway

See :mod:`pyweb.deploy.plan` for how decisions are made and
:mod:`pyweb.deploy.targets` for the files.
"""

from .legacy import caddyfile, compose, dockerfile, expand_env, k8s_manifest, required_env
from .plan import PLATFORMS, TARGETS, Facts, Plan, describe, make_plan, read_app
from .targets import files_for, next_steps

__all__ = ["read_app", "make_plan", "describe", "files_for", "next_steps", "Plan", "Facts", "TARGETS",
           "PLATFORMS", "dockerfile", "compose", "caddyfile", "k8s_manifest", "expand_env", "required_env"]
