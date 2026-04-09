FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_leave_server.py .

# MCP server listens on port 8001 (streamable-http transport)
EXPOSE 8001

ENV MCP_PORT=8001
ENV PG_HOST=postgres.postgres.svc.cluster.local
ENV PG_PORT=5432
ENV PG_DB=hrdb
ENV PG_USER=hruser
ENV PG_PASSWORD=hrpassword

CMD ["python", "mcp_leave_server.py"]
