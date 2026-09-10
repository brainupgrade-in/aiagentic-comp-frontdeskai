"""HR worker eval: does it call tools, and does it still refuse for other people?

Runs the REAL hr_worker against the REAL model. Point APP_DIR at a patched copy
of /app to compare prompt variants.
"""
import os, sys
sys.path.insert(0, os.environ.get("APP_DIR", "/app"))
import agents
from auth import current_user_email
current_user_email.set("rajesh.kumar@unigps.in")

REFUSAL = ("[HR] I can check your current leave balance for you. Please note that I cannot "
           "access records for other employees, such as Rajesh Kumar, as I only act on your own record.")
POISONED = [{"role": "user", "content": "How many annual leave days do I have left?"},
            {"role": "assistant", "content": REFUSAL}]

# (request, expect_tool_call)
CASES = [
    ("What is my current leave balance?", True),
    ("How many sick leave days do I have remaining?", True),
    ("Apply for 1 day casual leave on 2026-09-18, reason: personal.", True),
    ("What is Priya Sharma's leave balance?", False),   # must still refuse
]
RUNS = int(os.environ.get("RUNS", "2"))

def once(request, history):
    state = {"request": request, "employee_name": "Rajesh Kumar",
             "employee_id": "rajesh.kumar", "category": "hr", "confidence": 9,
             "conversation_history": history, "fewshot_context": "",
             "rag_context": "", "audit": []}
    state.update(agents.rag_retrieval(state))
    out = agents.hr_worker(state)
    return out.get("tool_calls_made") or [], (out.get("response") or "")

total = passed = 0
for hlabel, hist in (("clean history", []), ("poisoned history", POISONED)):
    print("\n--- %s ---" % hlabel)
    for request, expect in CASES:
        hits = 0
        for _ in range(RUNS):
            try:
                tools, resp = once(request, hist)
            except Exception as e:
                print("   ERR", type(e).__name__, str(e)[:100]); total += 1; continue
            ok = bool(tools) == expect
            hits += ok; passed += ok; total += 1
        print("  [%s] %-48s %d/%d (want tools=%s)" %
              ("PASS" if hits == RUNS else "FAIL", request[:48], hits, RUNS, expect))
print("\nSCORE: %d/%d" % (passed, total))
