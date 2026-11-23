"""Runs the 10 hero tickets on one shared store, then shows the approval queue and approves one item.

  python -m app.demo                 scripted model, no key needed
  python -m app.demo --mode replay   recorded real model outputs
"""
from __future__ import annotations

import argparse
import json

from app.actions import ActionDenied, ActionService
from app.llm import make_llm
from app.mock_systems import MockSystems
from app.pipeline import Env, run_ticket
from data.seed import load_tickets, seeded_store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="scripted", choices=["scripted", "replay", "live", "auto"])
    args = ap.parse_args()
    llm, mode = make_llm(args.mode)
    env, store = Env(llm=llm, mode=mode, run_id="demo"), seeded_store()
    print(f"mode: {mode}\n")
    print(f"{'ticket':7}{'intent':20}{'rule':8}{'disposition':18}actions")
    for t in [t for t in load_tickets() if "hero" in t["tags"]]:
        tr = run_ticket(t, env, store=store)
        intent = (tr["extraction"] or {}).get("intent", "-")
        acts = ", ".join(f"{a['name']}({a['status']})" for a in tr["actions"]) or "-"
        print(f"{tr['ticket_id']:7}{intent:20}{tr['rule_id']:8}{tr['disposition']:18}{acts}")
        if tr["security_alerts"]:
            print(f"{'':7}security alert: {tr['security_alerts'][0]}")

    svc = ActionService(store, MockSystems(store))
    print("\nApproval queue (highest risk first). Each item shows the facts, the action and the rule:")
    for p in svc.pending():
        print(f"  #{p['approval_id']} risk {p['risk_score']:.2f}  {p['summary']}")
    top = svc.pending()[0]
    print(f"\ncx_associate approves #{top['approval_id']} through the same action service the agent uses:")
    try:
        print("  result:", json.dumps(svc.approve(top["approval_id"], actor="amy", role="cx_associate")))
    except ActionDenied as e:
        print("  denied:", e)
    print("\nLast 5 audit log rows (append-only):")
    for r in store.q("SELECT actor, role, action, rule_id, outcome FROM audit_log ORDER BY id DESC LIMIT 5"):
        print(f"  {r['actor']:6}{r['role']:14}{r['action']:28}{r['rule_id']:14}{r['outcome']}")


if __name__ == "__main__":
    main()
