"""Shim for hermes_logging — missing from the hermes-agent 0.7.0 package.

The hermes-agent package imports this module but doesn't include it in
its distribution. This provides the minimal stubs needed at runtime.
"""
import logging


def setup_logging(**kwargs):
    logging.basicConfig(level=logging.WARNING)


def setup_verbose_logging(**kwargs):
    logging.basicConfig(level=logging.DEBUG)
