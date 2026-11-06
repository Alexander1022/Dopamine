# Security

Dopamine is a local testing proxy: it decrypts HTTPS with a certificate authority it creates on your Mac, and stores captured traffic, including credentials and cookies, in project files (`.dopamine`, by default in `~/Documents/Dopamine Projects`). Treat those files and `~/Library/Application Support/Dopamine` as sensitive, and remove trust for the Dopamine certificate when you stop using it.

## Reporting a vulnerability

Please report vulnerabilities in Dopamine itself (for example, the engine or MCP endpoint reachable from outside `127.0.0.1`, CA key exposure, or a way for captured content to run script in the app) through GitHub's **Report a vulnerability** button on the Security tab of this repository, not in a public issue.

Include the Dopamine version, macOS version, and steps to reproduce. Fixes are made on the latest release only.

## Scope

Use Dopamine only on systems you own or are authorized to test. Reports about Dopamine being able to intercept traffic you route through it are expected behavior, not vulnerabilities.
