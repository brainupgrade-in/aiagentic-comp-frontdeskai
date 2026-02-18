#!/bin/bash
# Generate test traffic for FrontDesk AI to populate observability dashboards.
# Uses varied questions across categories to produce diverse metrics.
# Includes delays between requests to avoid overloading Groq API.
#
# Usage:
#   bash scripts/generate-test-traffic.sh                    # default: 10 requests, 15s delay
#   bash scripts/generate-test-traffic.sh 5                  # 5 requests, 15s delay
#   bash scripts/generate-test-traffic.sh 20 30              # 20 requests, 30s delay
#   FRONTDESKAI_URL=http://localhost:8000 bash scripts/generate-test-traffic.sh

set -euo pipefail

NUM_REQUESTS="${1:-10}"
DELAY_SECONDS="${2:-15}"
BASE_URL="${FRONTDESKAI_URL:-http://localhost:8000}"
EMAIL="loadtest@test.com"
PASSWORD="brainupgrade"

# Diverse questions across all categories to generate varied metrics
QUESTIONS=(
  "What is the leave policy?"
  "My laptop screen is flickering, what should I do?"
  "How do I submit an expense report?"
  "Can you book a conference room for tomorrow?"
  "What are the working hours?"
  "I forgot my VPN password, how do I reset it?"
  "When is the next pay day?"
  "The office printer on floor 3 is jammed"
  "How do I request a salary advance?"
  "Is there a gym membership benefit?"
  "My monitor is not connecting to the docking station"
  "What is the maternity leave policy?"
  "How do I set up direct deposit?"
  "The AC in meeting room B is too cold"
  "Can I work from home on Fridays?"
  "I need access to the company wiki"
  "What holidays do we get this year?"
  "How do I order new business cards?"
  "The elevator on the east wing is stuck"
  "What is the process for internal transfers?"
)

echo "==> FrontDesk AI Test Traffic Generator"
echo "    URL:      ${BASE_URL}"
echo "    Requests: ${NUM_REQUESTS}"
echo "    Delay:    ${DELAY_SECONDS}s between requests"
echo ""

# Login and get cookie
COOKIE_FILE=$(mktemp)
trap "rm -f ${COOKIE_FILE}" EXIT

echo "==> Logging in as ${EMAIL}..."
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" \
  -c "${COOKIE_FILE}" \
  -X POST "${BASE_URL}/login" \
  -d "email=${EMAIL}&password=${PASSWORD}")

if [ "${HTTP_CODE}" != "200" ] && [ "${HTTP_CODE}" != "302" ]; then
  echo "ERROR: Login failed with HTTP ${HTTP_CODE}"
  exit 1
fi
echo "    Login successful"
echo ""

SUCCESS=0
ERRORS=0

for i in $(seq 1 "${NUM_REQUESTS}"); do
  # Pick a question (cycle through the list)
  IDX=$(( (i - 1) % ${#QUESTIONS[@]} ))
  QUESTION="${QUESTIONS[$IDX]}"

  echo -n "[${i}/${NUM_REQUESTS}] \"${QUESTION}\" ... "

  START=$(date +%s%N)
  RESPONSE=$(curl -sk -w "\n%{http_code}" \
    -b "${COOKIE_FILE}" \
    -X POST "${BASE_URL}/chat/send" \
    -d "message=${QUESTION}" \
    --max-time 60)

  HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
  BODY=$(echo "${RESPONSE}" | sed '$d')

  END=$(date +%s%N)
  DURATION_MS=$(( (END - START) / 1000000 ))

  if [ "${HTTP_CODE}" = "200" ]; then
    CATEGORY=$(echo "${BODY}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('category','?'))" 2>/dev/null || echo "?")
    ESCALATED=$(echo "${BODY}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('escalated',False))" 2>/dev/null || echo "?")
    echo "OK (${DURATION_MS}ms, category=${CATEGORY}, escalated=${ESCALATED})"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "FAILED (HTTP ${HTTP_CODE}, ${DURATION_MS}ms)"
    ERRORS=$((ERRORS + 1))
  fi

  # Delay between requests to avoid Groq rate limits
  if [ "${i}" -lt "${NUM_REQUESTS}" ]; then
    sleep "${DELAY_SECONDS}"
  fi
done

echo ""
echo "==> Done: ${SUCCESS} success, ${ERRORS} errors out of ${NUM_REQUESTS} requests"
