"""Shared Trackio dashboard logging for standalone training examples."""

import logging


def log_trackio_dashboard() -> None:
    """Log the current user's Trackio dashboard when identity lookup succeeds."""
    try:
        from huggingface_hub import whoami

        hf_user = whoami().get("name")
        if hf_user:
            logging.info(f"Trackio dashboard (live training progress): https://huggingface.co/spaces/{hf_user}/trackio")
    except Exception:
        logging.debug("Trackio dashboard URL unavailable")
