"""Streamlit UI application."""

from __future__ import annotations

import os

import streamlit as st

from ..core.facade import Facade

# Allow config path to be passed via environment variable
_CONFIG_ENV = "MYPROJECT__CONFIG_PATH"


def main() -> None:
    """Entry point for the Streamlit UI."""
    # st.set_page_config must be the first st command
    st.set_page_config(page_title="myproject")

    yaml_path = os.environ.get(_CONFIG_ENV)
    if yaml_path:
        cfg = Facade(yaml_path).configs
    else:
        cfg = None

    st.title("myproject")

    if cfg is not None:
        st.markdown(f"**app_name:** {cfg.app_name}")
        st.markdown(f"**paths.config:** `{cfg.paths.config}`")
        st.markdown(f"**paths.data:** `{cfg.paths.data}`")
        st.markdown(f"**paths.state:** `{cfg.paths.state}`")
        st.markdown(f"**paths.cache:** `{cfg.paths.cache}`")
    else:
        st.info("No configuration loaded. Set MYPROJECT__CONFIG_PATH env var to load a config.")


if __name__ == "__main__":
    main()
