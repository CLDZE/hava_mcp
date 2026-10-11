# Security Policy

## Supported Versions

Security fixes are applied to the latest released version only.

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| < 0.4   | No        |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately using either:

- GitHub's [private vulnerability reporting](https://github.com/CLDZE/hava_mcp/security/advisories/new) (preferred), or
- Email: oleggo@cloudzone.io

Please include:

- A description of the issue and its potential impact
- Steps to reproduce, or a proof of concept
- Affected version(s) and environment (OS, Python version, MCP client)

## Response Process

- Acknowledgement within 3 business days
- Initial assessment within 7 business days
- We will keep you updated on progress and coordinate a disclosure timeline with you. Fixes are released before public disclosure, and reporters are credited unless they prefer otherwise.

## Scope

This policy covers the `hava-mcp` server in this repository. It handles Hava API tokens and can create sources, update credentials and create API tokens, so issues of particular interest include:

- Leakage or logging of API tokens or cloud credentials
- Insecure handling of environment variables or local files
- Injection or request-forgery issues in tool inputs
- Vulnerable dependencies that are exploitable through this server

Vulnerabilities in the Hava platform or API itself should be reported to Hava (hava.io) directly.

## Security Recommendations for Users

- Store your Hava API token in environment variables (see `.env.example`); never commit `.env` files.
- Use the least-privileged token available and rotate it regularly.
- Keep dependencies up to date.
