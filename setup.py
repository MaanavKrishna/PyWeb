"""Packaging hook: ship the documentation inside the package (pyweb/ai/docs/), so `pyweb mcp` can
search and serve it after `pip install`. Everything else is configured in pyproject.toml."""

import glob
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = os.path.dirname(os.path.abspath(__file__))


class BuildWithDocs(build_py):
    def run(self):
        super().run()
        target = os.path.join(self.build_lib, "pyweb", "ai", "docs")
        os.makedirs(target, exist_ok=True)
        for path in sorted(glob.glob(os.path.join(HERE, "docs", "*.md"))) + [os.path.join(HERE, "THREAT_MODEL.md")]:
            if os.path.isfile(path):
                shutil.copy(path, target)


setup(cmdclass={"build_py": BuildWithDocs})
