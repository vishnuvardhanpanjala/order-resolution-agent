"""Tests for the code checks that run on every drafted reply."""
from app.verify import run_checks

POINTS = ["Order O1001 has shipped with LionPost.", "Tracking number: SG10010001.", "Expected delivery: 22 Nov 2026."]


def failed(reply, points=POINTS, done=frozenset(), name="Tan Wei Ling"):
    return {c["name"] for c in run_checks(reply, points, set(done), name) if not c["pass"]}


def test_grounded_reply_passes():
    assert failed("Hi Tan Wei Ling, O1001 shipped with LionPost (SG10010001) and should arrive on Nov 22.") == set()


def test_invented_date_amount_and_tracking_fail():
    assert "dates_grounded" in failed("It will arrive 25 Nov.")
    assert "amounts_grounded" in failed("We'll also give you S$10 off.")
    assert "tracking_numbers_grounded" in failed("Tracking SG99990001.")


def test_other_order_numbers_and_phone_numbers_fail():
    assert "order_numbers_grounded" in failed("Your order O1003 is fine too.")
    assert "phone_numbers_grounded" in failed("Call us on 9123 4567.")


def test_links_and_emails_must_be_allowlisted():
    assert "links_allowlisted" in failed("Track it at https://evil.example/track")
    assert failed("Return label: https://pebblewalk.example/returns/RL-1", POINTS + [
        "Your free return label: https://pebblewalk.example/returns/RL-1"]) == set()
    assert "emails_allowlisted" in failed("Write to claims@parcel-help.example")
    assert "emails_allowlisted" not in failed("Write to support@pebblewalk.example")


def test_cannot_claim_a_refund_that_did_not_happen():
    assert "no_false_claims" in failed("We have refunded you in full.")
    pts = ["We have refunded S$59.00 to your card."]
    assert "no_false_claims" not in failed("We've refunded S$59.00 to your card.", pts, done={"issue_refund"})


def test_internal_terms_never_reach_the_customer():
    assert "no_internal_terms" in failed("Your ticket was flagged as RISK by the system prompt.")


def test_street_names_are_not_mistaken_for_dates():
    pts = ["The delivery address for order O1054 is now: 8 Marina Blvd, Singapore 018981."]
    assert failed("Done: O1054 now goes to 8 Marina Blvd, Singapore 018981.", pts) == set()


def test_empty_reply_fails():
    assert failed("") == {"not_empty"}
