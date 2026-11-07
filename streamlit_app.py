"""Streamlit UI wrapper over the CLI pipeline, for the walkthrough demo.

This is a thin presentation layer only -- all logic lives in oltaflock.pipeline /
oltaflock.engine / oltaflock.llm_azure, unchanged. Run with:

    streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

import streamlit as st

logger = logging.getLogger("oltaflock.streamlit")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from oltaflock.engine import load_rate_sheet  # noqa: E402
from oltaflock.pipeline import run_inquiry  # noqa: E402

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"

st.set_page_config(page_title="Oltaflock Travel Proposal Engine", layout="wide")
st.title("From client inquiry to priced proposal")
st.caption(
    "Prices come only from the rate sheet below. The AI never invents or adjusts a "
    "price -- anything the sheet can't satisfy is marked 'to be quoted'."
)


@st.cache_data
def _load_rate_sheet_rows():
    return load_rate_sheet(DATA_DIR / "rate_sheet.csv")


@st.cache_data
def _load_sample_inquiries():
    return json.loads((DATA_DIR / "inquiries.json").read_text(encoding="utf-8"))


with st.expander("Rate sheet (the only source of prices)", expanded=False):
    rows = _load_rate_sheet_rows()
    st.table(
        [
            {
                "Hotel": r.name,
                "City": r.city,
                "Room": r.room_type,
                "Sleeps": r.sleeps,
                f"{r.currency}/night": r.price_per_night,
                "Wheelchair accessible": r.wheelchair_accessible,
                "Quiet zone": r.quiet_zone,
                "Adults only": r.adults_only,
            }
            for r in rows
        ]
    )

samples = _load_sample_inquiries()
sample_labels = {s["id"]: s["text"] for s in samples}

col_left, col_right = st.columns([1, 1])

with col_left:
    choice = st.selectbox(
        "Load a sample inquiry (or write your own below)",
        options=["-- write your own --"] + list(sample_labels.keys()),
    )
    default_text = "" if choice == "-- write your own --" else sample_labels[choice]
    inquiry_text = st.text_area("Client inquiry", value=default_text, height=160)
    inquiry_id = st.text_input(
        "Inquiry id (used for logging/audit only)",
        value=choice if choice != "-- write your own --" else "manual_inquiry",
    )
    run_clicked = st.button("Generate proposal", type="primary", disabled=not inquiry_text.strip())

with col_right:
    if run_clicked:
        rate_sheet = _load_rate_sheet_rows()
        with st.spinner("Calling Azure OpenAI and running the deterministic pricing engine..."):
            try:
                result = run_inquiry(inquiry_id, inquiry_text, rate_sheet)
            except Exception as exc:
                # Same fix as webapp/main.py: internal exception details are logged
                # server-side only, never shown to the advisor directly.
                error_id = uuid.uuid4().hex[:8]
                logger.exception("run_inquiry failed [error_id=%s] inquiry_id=%s", error_id, inquiry_id)
                st.error(f"Couldn't generate a proposal right now (reference {error_id}).")
                st.stop()

        st.subheader("Structured trip brief")
        st.json(result.brief.model_dump(mode="json"))

        if any(leg.notes for leg in result.info_audit.legs):
            for leg in result.info_audit.legs:
                for note in leg.notes:
                    st.caption(f"ℹ️ {leg.destination}: {note}")

        st.subheader("Questions for the client")
        if result.client_questions.questions:
            for q in result.client_questions.questions:
                st.markdown(f"- {q}")
        else:
            st.markdown("_Nothing missing or unclear._")

        st.subheader("Priced proposal")
        st.markdown(f"**Status:** `{result.proposal.status}`")
        for leg in result.proposal.legs:
            with st.container(border=True):
                st.markdown(f"**{leg.destination}** — `{leg.status}`")
                if leg.status == "priced" and leg.line_item:
                    st.markdown(
                        f"{leg.hotel_name} · {leg.line_item.nights} nights × "
                        f"${leg.line_item.rate_per_night:.2f} = **${leg.line_item.subtotal:.2f}**"
                    )
                    if leg.budget_exceeded:
                        st.warning("This leg exceeds the client's stated budget.")
                    if leg.assumed_fields:
                        st.caption(f"Assumed (not confirmed by client): {', '.join(leg.assumed_fields)}")
                else:
                    st.markdown("**To be quoted.** Reasons:")
                    for r in leg.unmet_reasons:
                        st.markdown(f"- {r}")

        if result.proposal.total_price_usd is not None:
            st.markdown(f"### Total: ${result.proposal.total_price_usd:.2f}")
        elif result.proposal.priced_subtotal_usd is not None:
            st.markdown(
                f"### Priced legs only (incomplete trip): ${result.proposal.priced_subtotal_usd:.2f}"
            )

        if result.proposal.day_by_day_outline:
            st.subheader("Day-by-day outline")
            for line in result.proposal.day_by_day_outline:
                st.markdown(f"- {line}")

        if result.proposal.narration:
            st.subheader("Advisor-facing summary")
            st.write(result.proposal.narration)

        with st.expander("Raw JSON result"):
            st.json(result.model_dump(mode="json"))
    else:
        st.info("Pick a sample inquiry or write your own, then click **Generate proposal**.")
