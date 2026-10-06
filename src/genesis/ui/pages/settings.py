"""Settings page: backup and restore for the signed in user's data.

Two expanders expose ``facade.backup``:

- "Download backup" runs ``export_to_bytes`` on demand and offers the
  result as ``st.download_button``.
- "Restore from backup" accepts a zip via ``st.file_uploader``,
  validates it, requires the user to acknowledge the destructive
  intent, then runs ``import_from_bytes``.

Validation, destructive confirmation, and the resulting counts are
all rendered in place so the user has enough information to make
the call before clicking. ADR-024.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from genesis.backup import ArchiveError, ValidationReport
from genesis.ui._state import get_facade

facade = get_facade()
backup = facade.backup

st.title("Settings")
st.caption("Account and data management.")

# Default file name for downloads. The user can override through the
# widget; rendering the suggested name in the caption keeps it visible
# without occupying form real estate.
_default_filename = f"genesis-backup-{facade.configs.user}-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".zip"


def _render_validation(report: ValidationReport) -> None:
    """Render the manifest and any issues found during validation."""
    if report.manifest is not None:
        m = report.manifest
        st.markdown(
            f"**Schema version:** `{m.schema_version}`  \n"
            f"**Generator:** `{m.generator}`  \n"
            f"**Username:** `{m.username}`  \n"
            f"**Created at:** `{m.created_at.isoformat()}`  \n"
            f"**Sections present:** {sorted(report.present_sections) or 'none'}"
        )
        counts = m.counts
        st.markdown(
            "**Counts (from manifest):** "
            f"projects `{counts.projects}`, tasks `{counts.tasks}`, "
            f"events `{counts.events}`, time_log `{counts.time_log}`, "
            f"journal `{counts.journal}`"
        )

    if report.issues:
        for issue in report.issues:
            st.error(issue)
    elif report.ok:
        st.success("Archive passed structural validation.")


# -----------------------------------------------------------------------------
# Download backup
# -----------------------------------------------------------------------------


with st.expander("Download backup", expanded=False):
    st.write(
        "Build a zip archive of every task, project, calendar event, "
        "time log entry, and journal entry on your account. Save the "
        "file somewhere safe."
    )
    filename = st.text_input(
        "File name",
        value=_default_filename,
        key="settings_backup_filename",
        help="The file will be offered for download with this name.",
    )

    build_clicked = st.button(
        "Prepare backup",
        key="settings_backup_prepare",
        type="secondary",
    )

    if build_clicked or "settings_backup_bytes" in st.session_state:
        if build_clicked:
            with st.spinner("Building backup archive..."):
                st.session_state["settings_backup_bytes"] = backup.export_to_bytes()
        st.download_button(
            "Download backup archive",
            data=st.session_state["settings_backup_bytes"],
            file_name=filename or _default_filename,
            mime="application/zip",
            key="settings_backup_download",
            type="primary",
        )

# -----------------------------------------------------------------------------
# Restore from backup
# -----------------------------------------------------------------------------


with st.expander("Restore from backup", expanded=False):
    st.warning(
        "Restoring from a backup **overwrites** every task, project, "
        "calendar event, time log entry, journal entry, and your profile "
        "and life story (including their edit history) currently on your "
        "account. A snapshot of your current data is saved automatically "
        "before the overwrite, so you can recover if the archive turns "
        "out to be wrong."
    )
    st.write(
        "We strongly recommend downloading the current state from the Download backup section above before restoring."
    )

    uploaded = st.file_uploader(
        "Choose a backup zip",
        type=["zip"],
        key="settings_restore_upload",
    )

    validation_holder = st.empty()
    action_holder = st.empty()

    if uploaded is not None:
        uploaded_bytes = uploaded.getvalue()
        with st.spinner("Validating archive..."):
            validation = backup.validate_archive(uploaded_bytes)

        with validation_holder.container():
            _render_validation(validation)

        if not validation.ok:
            st.error(
                "Restore is disabled until the issues above are "
                "resolved. Pick a different archive or fix the problem "
                "before continuing."
            )
        else:
            with action_holder.container():
                confirmed = st.checkbox(
                    "I understand this will overwrite my current data.",
                    key="settings_restore_confirm",
                )
                restore_clicked = st.button(
                    "Restore from this archive",
                    key="settings_restore_button",
                    type="primary",
                    disabled=not confirmed,
                )
                if restore_clicked:
                    placeholder = st.empty()
                    placeholder.info("Restoring. This can take a few seconds.")
                    try:
                        report = backup.import_from_bytes(uploaded_bytes)
                    except ArchiveError as exc:
                        placeholder.error(f"Restore aborted: {exc}")
                    except Exception as exc:  # surface unexpected failures
                        placeholder.error(
                            f"Restore failed with an unexpected error. Your pre-restore snapshot is intact: {exc}"
                        )
                    else:
                        placeholder.empty()
                        st.success("Restore complete.")
                        st.markdown(
                            "**Restored counts:** "
                            f"projects `{report.projects}`, "
                            f"tasks `{report.tasks}`, "
                            f"events `{report.events}`, "
                            f"time_log `{report.time_log}`, "
                            f"journal `{report.journal}`"
                        )
                        if report.safety_backup is not None:
                            st.markdown(
                                f"**Pre-restore snapshot saved to:** "
                                f"`{report.safety_backup}` "
                                "— keep it until you are confident "
                                "the restore is what you wanted."
                            )
                        # Drop the cached bytes so a rerun does not
                        # redownload the previous build right under
                        # the new restore report.
                        st.session_state.pop("settings_backup_bytes", None)


# -----------------------------------------------------------------------------
# Notifications (ntfy)
# -----------------------------------------------------------------------------


with st.expander("Notifications", expanded=False):
    """Show the resolved ntfy URL and topic. Read-only this round.

    The focus page POSTs to ``{url.rstrip('/')}/{topic}`` when a
    session starts, every 30 minutes, and when a countdown
    auto-stops. The URL and topic are configured in
    ``configs.yaml`` under the ``ntfy:`` key; changes take effect
    on the next Facade construction. There is no edit surface
    in the UI this round (ADR-027).

    ``resolved_topic`` returns the explicit topic when set and
    otherwise builds ``genesis-<safe_host>-<user>`` so two
    machines sharing one ntfy server get different topics by
    default.
    """
    ntfy_cfg = facade.configs.ntfy
    resolved_topic = ntfy_cfg.resolved_topic(facade.configs.user)
    st.markdown(f"**Server URL:** `{ntfy_cfg.url}`")
    st.markdown(f"**Topic:** `{resolved_topic}`")
    st.caption(
        "Edit `ntfy.url` and `ntfy.topic` in `configs.yaml` under the "
        "`ntfy:` key, or override with `GENESIS__NTFY__URL` / "
        "`GENESIS__NTFY__TOPIC`. Changes apply on the next session."
    )
