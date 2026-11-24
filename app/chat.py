"""Talk to the agent in a terminal, as one of the test customers.

    python -m app.chat                       # logged in as C001, Tan Wei Ling
    python -m app.chat --customer C013       # someone else
    python -m app.chat --mode scripted       # no key: the keyword stand-in model (crude, for trying the flow only)

Every message runs the full pipeline on the whole conversation so far, on one copy of the test database
that lasts for the session, so a cancel or a refund sticks. Under each reply it shows what happened behind
the scenes: the rule, the actions, anything waiting for staff, and any quiet risk alert.

The clock is fixed at Friday 20 Nov 2026, 10:00 Singapore time, the same as the test tickets.
Commands: /orders  /photo  /new  /queue  /approve <id>  /who <customer id>  /quit
"""
from __future__ import annotations

import argparse
import os
import sys

from app.actions import ActionDenied, ActionService
from app.llm import ROOT, OpenAICompatClient, RecordingClient, load_dotenv
from app.mock_systems import MockSystems
from app.pipeline import Env, run_ticket
from app.policy import load_policy_config
from app.scripted import ScriptedLLM
from data.seed import seeded_store

CHAT_RECORDINGS = ROOT / "recordings" / "chat.jsonl"  # kept apart from the eval recordings, and git-ignored


def make_chat_llm(mode: str):
    if mode == "auto":
        load_dotenv()
        mode = "live" if os.environ.get("GROQ_API_KEY") else "scripted"
    if mode == "scripted":
        return ScriptedLLM(), "scripted"
    return RecordingClient(OpenAICompatClient(), path=CHAT_RECORDINGS), "live"


def show_orders(store, cid: str) -> None:
    rows = store.q("SELECT order_id FROM orders WHERE customer_id=? ORDER BY order_id", cid)
    for r in rows:
        o = store.order_view(r["order_id"])
        items = ", ".join(f"{l['name']} size {l['size']}" + (" (final sale)" if l["final_sale"] else "")
                          for l in o["lines"])
        when = f"delivered {str(o['delivered_at'])[:10]}" if o.get("delivered_at") else "not delivered yet"
        print(f"  {o['order_id']}  {o['status']:<9} {when:<22} S${o['total']:.2f}  {items}")


def behind_the_scenes(tr: dict) -> str:
    acts = ", ".join(f"{a['name']} {('S$%.2f ' % a['amount']) if a.get('amount') else ''}({a['status']})"
                     for a in tr["actions"]) or "none"
    lines = [f"  rule {tr['rule_id']} -> {tr['disposition']}" + (f" ({tr['reason_code']})" if tr["reason_code"]
                                                                  else ""),
             f"  actions: {acts}"]
    e = tr.get("extraction") or {}
    if e:
        lines.append(f"  model read: {e.get('intent')} at {e.get('confidence')}, order {e.get('order_id') or 'none'}"
                     + (" (order number taken from the text by code)" if tr.get("order_from_text") else ""))
    for a in tr["security_alerts"]:
        lines.append(f"  quiet alert to the risk team: {a.get('handled')}")
    if tr["reply_held"]:
        lines.append("  the draft failed its checks, so a safe fixed reply went out instead")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chat with the agent as a test customer.")
    ap.add_argument("--customer", default="C001")
    ap.add_argument("--mode", choices=["auto", "live", "scripted"], default="auto")
    args = ap.parse_args(argv)
    llm, mode = make_chat_llm(args.mode)
    store = seeded_store()
    env = Env(llm=llm, mode=mode, run_id="chat")
    service = ActionService(store, MockSystems(store), load_policy_config())
    cid, thread, turn, photo = args.customer.upper(), [], 0, False

    def who(c):
        row = store.one("SELECT name FROM customers WHERE customer_id=?", c)
        if not row:
            print(f"  no customer {c}. Test customers are C001 to C060.")
            return False
        print(f"\nLogged in as {c}, {row['name']}. Model: {mode}. Their orders:")
        show_orders(store, c)
        return True

    if not who(cid):
        return 1
    print("\nType a message as the customer. /orders /photo /new /queue /approve <id> /who <id> /quit\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        if cmd == "/quit":
            return 0
        if cmd == "/orders":
            show_orders(store, cid)
            continue
        if cmd == "/photo":
            photo = True
            print("  a photo will be attached to your next message")
            continue
        if cmd == "/new":
            thread = []
            print("  new conversation")
            continue
        if cmd == "/who":
            if who(arg.strip().upper() or cid):
                cid, thread = arg.strip().upper() or cid, []
            continue
        if cmd == "/queue":
            for p in service.pending() or [{"approval_id": "-", "summary": "nothing waiting"}]:
                print(f"  #{p['approval_id']}  {p['summary']}")
            continue
        if cmd == "/approve":
            try:
                print("  done:", service.approve(int(arg), actor="you", role="cx_associate"))
            except (ActionDenied, ValueError) as e:
                print(f"  not approved: {e}")
            continue
        turn += 1
        thread.append({"role": "customer", "text": text})
        ticket = {"ticket_id": f"CHAT-{cid}-{turn}", "channel": "web_chat", "authenticated_customer_id": cid,
                  "messages": list(thread), "attachments": [{"type": "image"}] if photo else []}
        photo = False
        tr = run_ticket(ticket, env, store=store)
        thread.append({"role": "agent", "text": tr["reply"]})
        print("\n" + tr["reply"] + "\n")
        print(behind_the_scenes(tr) + "\n")


if __name__ == "__main__":
    sys.exit(main())
