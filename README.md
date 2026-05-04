# Hi, I'm Tamir Marom 👋

I build backend systems focused on security and infrastructure.

---

## Featured Project — [Cloud-Continuum-FireWall](https://github.com/MaromTamir/Cloud-Continuum-FireWall)

A dual-layer firewall system for HTTP services: an IP allowlist enforced at the application layer (Node.js + PostgreSQL) combined with OS-level Windows Firewall rules as a second line of defense.

**How it works:**
- Every inbound request is checked against a `firewall_rules` table; unlisted IPs receive `403 Forbidden`
- Admin endpoints (`/admin/firewall`) are token-gated and sit outside the IP filter so the allowlist can be bootstrapped remotely
- PowerShell scripts apply and revert matching Windows Firewall rules at the host level, blocking all inbound traffic by default and allowing only listed IPs on the service port

**Stack:** Node.js · Express · PostgreSQL · PowerShell

---

## Skills

- **Backend:** Node.js, Express, REST API design
- **Databases:** PostgreSQL
- **Security:** IP filtering, bearer-token auth, host firewall configuration
- **Infrastructure:** Windows Server, CI/CD (GitHub Actions)

---

📫 Reach me via [GitHub](https://github.com/tamirmarom)
