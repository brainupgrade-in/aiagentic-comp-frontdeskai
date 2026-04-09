# UniGPS IT Support Guide

## Service Level Agreements (SLA)

| Priority | Description | Response Time | Resolution Time |
|----------|-------------|---------------|-----------------|
| P1 — Critical | Production system down, data loss, security breach | 15 minutes | 1 hour |
| P2 — High | Major feature broken, multiple users affected | 30 minutes | 4 hours |
| P3 — Medium | Single user issue, workaround available | 2 hours | Next business day |
| P4 — Low | Enhancement request, cosmetic issue | 1 business day | 5 business days |

## How to Raise a Ticket

1. **Jira Service Desk**: https://jira.unigps.in/servicedesk — preferred method for all non-critical issues.
2. **Emergency Hotline**: Call ext. 5555 for P1 incidents only.
3. **Slack**: Post in #it-support for quick questions.
4. **Email**: itsupport@unigps.in (auto-creates a Jira ticket).

## VPN Access

### Setup
- Download the FortiClient VPN client from https://vpn.unigps.in/download.
- Configuration server: vpn.unigps.in:443
- Authentication: Use your company email and Active Directory password.
- Two-factor authentication (2FA): Required via Google Authenticator or Microsoft Authenticator.

### Troubleshooting
- **"Connection timed out"**: Check if you're on a restricted network (some hotel/airport WiFi blocks VPN ports). Try switching to mobile hotspot.
- **"Authentication failed"**: Reset your AD password at https://password.unigps.in. If 2FA fails, contact IT to reset your authenticator seed.
- **"VPN connected but can't access resources"**: Check if your VPN profile has the correct split-tunnel settings. Run `ipconfig /all` (Windows) or `ifconfig` (Mac) and share with IT.
- **Slow VPN**: Connect to the nearest VPN gateway (India: vpn-in.unigps.in, US: vpn-us.unigps.in).

## Software & Tools

### Standard Software Stack
| Tool | Purpose | Access |
|------|---------|--------|
| Jira | Project management & issue tracking | https://jira.unigps.in |
| Confluence | Documentation wiki | https://wiki.unigps.in |
| Slack | Team communication | Desktop + mobile app |
| GitHub Enterprise | Source code repository | https://github.unigps.in |
| AWS Console | Cloud infrastructure | SSO via https://aws.unigps.in |
| Jenkins | CI/CD pipelines | https://jenkins.unigps.in |
| SonarQube | Code quality analysis | https://sonar.unigps.in |
| Grafana | Monitoring dashboards | https://grafana.unigps.in |

### Requesting New Software
- Submit a Software Request ticket in Jira with business justification.
- IT reviews within 2 business days.
- Licensed software requires manager and finance approval.
- No unauthorized software installations — use the Company App Store.

## Hardware

### Standard Issue
- **Developers**: MacBook Pro 14" (M3, 16GB RAM, 512GB SSD) or equivalent ThinkPad.
- **Non-technical staff**: ThinkPad T-series or MacBook Air.
- **Monitors**: One 27" 4K monitor provided. Second monitor available on request.
- **Peripherals**: Wireless keyboard, mouse, and headset included.

### Hardware Issues
- **Laptop not booting**: Try a hard reset (hold power 10 seconds). If unresolved, bring to IT desk (3rd floor, Room 302).
- **Slow performance**: Run the IT Health Check tool (Start Menu → UniGPS IT Tools). Share the report with IT.
- **Damaged equipment**: Report immediately. Accidental damage covered under company insurance. Negligent damage may be employee's liability.

### Hardware Replacement
- Laptops replaced every 3 years or upon confirmed hardware failure.
- Submit a Hardware Request in Jira with the asset tag number.

## Email & Accounts

- Email: Microsoft 365 (Outlook). Access via https://outlook.office365.com.
- Password policy: Minimum 12 characters, must include uppercase, lowercase, number, and special character. Password expires every 90 days.
- Account lockout: After 5 failed attempts. Auto-unlocks after 30 minutes, or contact IT.
- Distribution lists: Request via Jira ticket. Manager approval required.

## Security Policies

- **Data Classification**: All company data is classified as Public, Internal, Confidential, or Restricted.
- **USB Devices**: External USB storage devices are blocked by default. Request exception via IT with manager approval.
- **Personal Devices**: BYOD allowed for email and Slack only, with MDM enrollment.
- **Phishing**: Report suspicious emails to security@unigps.in or click "Report Phishing" in Outlook.
- **Incident Response**: Security incidents must be reported to IT within 1 hour of discovery.

## AWS Access

- All AWS access is via SSO at https://aws.unigps.in.
- Developer accounts have read-only access by default.
- Write access to specific services requires a Jira ticket with manager approval.
- Production account access is restricted to the SRE team.
- Cost alerts: Set up in your AWS account. Budget overruns are escalated to your manager.

## Contact

- IT Service Desk: https://jira.unigps.in/servicedesk
- Emergency (P1): ext. 5555
- Email: itsupport@unigps.in
- Slack: #it-support
- Office: 3rd Floor, Room 302

## Oracle Cloud Infrastructure (OCI) Access

OCI is an approved cloud platform. Employees can manage OCI compute resources directly through
FrontDesk AI — no Jira ticket, no manager approval, no formal access request required.

Approved self-service actions for all employees:
- List and view OCI compute instances
- Restart, stop, or start instances
- Check OCI cloud spend and usage data
- Raise and track OCI support incidents

Admin-only actions (engineering leads, CTO):
- Launch new compute instances
- Terminate compute instances

OCI region: us-ashburn-1 | Compartment: 98634313-C06

This policy supersedes the AWS Access guidelines for OCI operations performed via FrontDesk AI.
