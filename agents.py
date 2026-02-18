"""Multi-agent support system based on Session 9 Lab 8."""

from typing import TypedDict, Annotated
from operator import add
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END

from observability import trace_llm_call

llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)


class SupportRequest(TypedDict):
    employee_name: str
    request: str
    category: str
    confidence: int
    worker_output: str
    needs_escalation: bool
    escalation_reason: str
    error: str
    fallback_used: bool
    final_response: str
    audit: Annotated[list, add]


# --- Supervisor ---

def supervisor(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are the UniGPS support desk supervisor.\n"
        f"Classify this employee request into: hr, tech, finance, facilities, general\n"
        f"Also rate your confidence 1-10.\n"
        f"Employee: {state['employee_name']}\n"
        f"Request: {state['request']}\n"
        f"Reply:\nCATEGORY: ...\nCONFIDENCE: ..."
    )
    try:
        with trace_llm_call("supervisor") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        text = response.content.lower()
        category = "general"
        confidence = 5
        for line in text.split("\n"):
            if "category:" in line:
                cat = line.split(":")[-1].strip()
                if cat in ["hr", "tech", "finance", "facilities", "general"]:
                    category = cat
            elif "confidence:" in line:
                try:
                    confidence = int(line.split(":")[-1].strip().rstrip("."))
                    confidence = max(1, min(10, confidence))
                except ValueError:
                    confidence = 5
        return {
            "category": category,
            "confidence": confidence,
            "error": "",
            "audit": [f"[{ts}] Supervisor: {category} (conf: {confidence})"],
        }
    except Exception as e:
        return {
            "category": "general",
            "confidence": 1,
            "error": "",
            "audit": [f"[{ts}] Supervisor error, fallback to general"],
        }


def route_supervisor(state: SupportRequest) -> str:
    if state["confidence"] < 5:
        return "clarify"
    return state["category"]


# --- Workers ---

def clarify_agent(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    return {
        "worker_output": f"Hi {state['employee_name']}, I'm not sure I understand your request: "
                         f"'{state['request']}'. Could you provide more details?",
        "needs_escalation": False,
        "error": "",
        "audit": [f"[{ts}] Clarification requested (conf: {state['confidence']})"],
    }


def hr_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are UniGPS HR (Gheware UniGPS Solutions LLP).\n"
        f"Policies: 24 casual leaves/year, 2 WFH days/week, family health insurance.\n"
        f"Employee: {state['employee_name']}\n"
        f"Request: {state['request']}\n"
        f"Reply helpfully in 2-3 sentences.\n"
        f"If this needs a policy exception (>10 days leave, special cases), "
        f"add on a new line: ESCALATE: <reason>"
    )
    try:
        with trace_llm_call("hr_worker") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        text = response.content.strip()
        escalate = False
        reason = ""
        if "ESCALATE:" in text.upper():
            escalate = True
            for line in text.split("\n"):
                if "ESCALATE:" in line.upper():
                    reason = line.split(":", 1)[-1].strip()
            text = text.split("ESCALATE:")[0].strip()
        return {
            "worker_output": text,
            "needs_escalation": escalate,
            "escalation_reason": reason,
            "error": "",
            "audit": [f"[{ts}] HR worker: {'escalating' if escalate else 'resolved'}"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] HR worker error: {e}"]}


def tech_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are UniGPS IT Support.\n"
        f"SLA: P1=1hr, P2=4hr, P3=next day. Tools: Jira, VPN, AWS.\n"
        f"Employee: {state['employee_name']}\n"
        f"Request: {state['request']}\n"
        f"Reply helpfully in 2-3 sentences.\n"
        f"If this is P1 severity (production down, data loss), add: ESCALATE: <reason>"
    )
    try:
        with trace_llm_call("tech_worker") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        text = response.content.strip()
        escalate = False
        reason = ""
        if "ESCALATE:" in text.upper():
            escalate = True
            for line in text.split("\n"):
                if "ESCALATE:" in line.upper():
                    reason = line.split(":", 1)[-1].strip()
            text = text.split("ESCALATE:")[0].strip()
        return {
            "worker_output": text,
            "needs_escalation": escalate,
            "escalation_reason": reason,
            "error": "",
            "audit": [f"[{ts}] Tech worker: {'escalating' if escalate else 'resolved'}"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] Tech worker error: {e}"]}


def finance_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are UniGPS Finance team.\n"
        f"Policies: expense claims within 30 days, salary on 1st of month, tax deadline March 31.\n"
        f"Employee: {state['employee_name']}\n"
        f"Request: {state['request']}\n"
        f"Reply helpfully in 2-3 sentences."
    )
    try:
        with trace_llm_call("finance_worker") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        return {
            "worker_output": response.content.strip(),
            "needs_escalation": False,
            "error": "",
            "audit": [f"[{ts}] Finance worker resolved"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] Finance worker error: {e}"]}


def facilities_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are UniGPS Facilities/Admin team.\n"
        f"You handle: desks, parking, cafeteria, access cards, building maintenance.\n"
        f"Employee: {state['employee_name']}\n"
        f"Request: {state['request']}\n"
        f"Reply helpfully in 2-3 sentences."
    )
    try:
        with trace_llm_call("facilities_worker") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        return {
            "worker_output": response.content.strip(),
            "needs_escalation": False,
            "error": "",
            "audit": [f"[{ts}] Facilities worker resolved"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] Facilities worker error: {e}"]}


def general_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    return {
        "worker_output": f"Hi {state['employee_name']}, your request has been logged. "
                         f"A team member will get back to you shortly.",
        "needs_escalation": False,
        "error": "",
        "audit": [f"[{ts}] General worker"],
    }


# --- Escalation, QA, Fallback, Finalize ---

def escalation_check(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    if state["error"]:
        return {"audit": [f"[{ts}] Worker errored, sending to fallback"]}
    return {"audit": [f"[{ts}] Escalation check: {state['needs_escalation']}"]}


def route_escalation(state: SupportRequest) -> str:
    if state["error"]:
        return "fallback"
    if state["needs_escalation"]:
        return "manager"
    return "qa_check"


def manager_agent(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    prompt = (
        f"You are a manager at UniGPS with authority over policy exceptions.\n"
        f"Escalation reason: {state['escalation_reason']}\n"
        f"Employee: {state['employee_name']}\n"
        f"Original request: {state['request']}\n"
        f"Worker's initial response: {state['worker_output'][:100]}\n\n"
        f"Provide a definitive answer with your authority. 2-3 sentences."
    )
    try:
        with trace_llm_call("manager") as ctx:
            response = llm.invoke(prompt)
            ctx["response"] = response
        return {
            "worker_output": f"[Manager Review] {response.content.strip()}",
            "error": "",
            "audit": [f"[{ts}] Manager resolved escalation"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] Manager error: {e}"]}


def qa_check(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    output = state["worker_output"]
    issues = []
    if len(output) < 20:
        issues.append("too short")
    if not output.strip():
        issues.append("empty")
    if issues:
        return {"error": ", ".join(issues), "audit": [f"[{ts}] QA FAIL: {issues}"]}
    return {"error": "", "audit": [f"[{ts}] QA PASS"]}


def route_qa(state: SupportRequest) -> str:
    return "fallback" if state["error"] else "finalize"


def fallback(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    templates = {
        "hr": "Please visit the HR portal or email hr@unigps.in.",
        "tech": "Please create a Jira ticket or contact IT at ext. 5555.",
        "finance": "Please email finance@unigps.in with your query.",
        "facilities": "Please email admin@unigps.in for facilities requests.",
        "general": "Your request has been noted. We'll respond shortly.",
    }
    output = templates.get(state["category"], templates["general"])
    return {
        "worker_output": output,
        "fallback_used": True,
        "error": "",
        "audit": [f"[{ts}] Fallback template: {state['category']}"],
    }


def finalize(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fallback_note = " (via fallback)" if state.get("fallback_used") else ""
    return {
        "final_response": (
            f"[{state['category'].upper()}] {state['worker_output']}\n"
            f"— UniGPS Support{fallback_note} | {ts}"
        ),
        "audit": [f"[{ts}] Finalized for {state['employee_name']}"],
    }


# --- Build Graph ---

def build_graph():
    graph = StateGraph(SupportRequest)

    graph.add_node("supervisor", supervisor)
    graph.add_node("clarify", clarify_agent)
    graph.add_node("hr_worker", hr_worker)
    graph.add_node("tech_worker", tech_worker)
    graph.add_node("finance_worker", finance_worker)
    graph.add_node("facilities_worker", facilities_worker)
    graph.add_node("general_worker", general_worker)
    graph.add_node("escalation_check", escalation_check)
    graph.add_node("manager", manager_agent)
    graph.add_node("qa_check", qa_check)
    graph.add_node("fallback", fallback)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges("supervisor", route_supervisor, {
        "clarify": "clarify",
        "hr": "hr_worker",
        "tech": "tech_worker",
        "finance": "finance_worker",
        "facilities": "facilities_worker",
        "general": "general_worker",
    })

    graph.add_edge("clarify", "finalize")
    for w in ["hr_worker", "tech_worker", "finance_worker", "facilities_worker", "general_worker"]:
        graph.add_edge(w, "escalation_check")

    graph.add_conditional_edges("escalation_check", route_escalation, {
        "manager": "manager",
        "qa_check": "qa_check",
        "fallback": "fallback",
    })

    graph.add_edge("manager", "qa_check")

    graph.add_conditional_edges("qa_check", route_qa, {
        "finalize": "finalize",
        "fallback": "fallback",
    })

    graph.add_edge("fallback", "finalize")
    graph.add_edge("finalize", END)

    return graph
