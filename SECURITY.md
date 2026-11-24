# Security

## Lab credentials are fake

The credentials in `.env.example` and in the lab stack (for example `floe-lab-access` /
`floe-lab-secret`) are deliberately fake and only work against the local lab. They are not
secrets. Never reuse them anywhere else, and never put a real credential in this repository.
AWS access always comes from your own profile or SSO session.

## Reporting a vulnerability

Report security issues privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
(the repository's **Security** tab, then **Report a vulnerability**). Do not open a public
issue. We will acknowledge your report and keep you updated until it is resolved.
