"""Streamlit UI application."""

from __future__ import annotations

import os

import streamlit as st

from ..configs import CONFIG_PATH_ENV, resolve_config_path
from ..core.facade import Facade


def main() -> None:
    """Entry point for the Streamlit UI."""
    # st.set_page_config must be the first st command
    st.set_page_config(page_title="genesis")

    yaml_path = os.environ.get(CONFIG_PATH_ENV) or str(resolve_config_path())
    cfg = Facade(yaml_path).configs

    st.title("genesis")

    st.markdown(f"**user:** {cfg.user}")
    st.markdown(f"**app_name:** {cfg.app_name}")
    st.markdown(f"**paths.config:** `{cfg.paths.config}`")
    st.markdown(f"**paths.data:** `{cfg.paths.data}`")
    st.markdown(f"**paths.state:** `{cfg.paths.state}`")
    st.markdown(f"**paths.cache:** `{cfg.paths.cache}`")


if __name__ == "__main__":
    main()
