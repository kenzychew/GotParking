"""Shared library code for the GotParking Vercel Python functions (T4).

Modules in this package are imported by the top-level endpoint files
(``api/batch_predict.py`` and ``api/forecast.py``). The leading underscore on
the ``_lib`` directory name tells Vercel's Python builder to skip it when
discovering serverless-function entrypoints, so nothing in here is
independently routable.
"""
