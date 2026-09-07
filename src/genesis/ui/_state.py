"""Cached Facade singleton for the Streamlit UI.

The Facade wraps Radicale startup and the CalDAV/CardDAV/Journal
sub-facades. Caching it via ``@st.cache_resource`` guarantees one
instance per Streamlit session, which in turn guarantees one
Radicale subprocess and one set of in-process connection objects.

The cache key is implicit: the function takes no arguments, so the
same Facade is returned for the lifetime of the session. To switch
users, restart the Streamlit process (the CLI's ``genesis --user X ui``
handles this).
"""

from __future__ import annotations

import os

import streamlit as st

from genesis.configs import CONFIG_PATH_ENV, resolve_config_path
from genesis.core.facade import Facade


@st.cache_resource
def get_facade() -> Facade:
    """Return the cached Facade, constructing it on first call.

    Honors ``GENESIS__CONFIG_PATH`` if set (the CLI uses this to hand
    the resolved config path to the UI subprocess). Otherwise falls
    back to ``resolve_config_path()`` which reads ``XDG_CONFIG_HOME``.
    """
    yaml_path = os.environ.get(CONFIG_PATH_ENV) or str(resolve_config_path())
    return Facade(yaml_path)
