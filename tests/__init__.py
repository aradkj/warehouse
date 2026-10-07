"""Test package root.

``tests`` is a package (not a set of loose modules) so that shared fixtures, fakes
and builders are importable as ``tests.<module>`` from every test module, no
matter which directory pytest is invoked from.
"""

from __future__ import annotations

__all__: list[str] = []
