# Security Policy

## Reporting a vulnerability

ODIN is provided as-is, without warranty or guaranteed support. Responses and fixes are
not guaranteed, so the most reliable protection is to run ODIN only in a trusted
environment, as described below.

If you do find a security issue, please report it privately through GitHub's private
vulnerability reporting (the **Security** tab → **"Report a vulnerability"**) rather than
in a public issue, and include enough detail to reproduce it.

## Deployment security model — read before deploying

**ODIN is designed to run on a single trusted machine (or a fully trusted network).
It must not be exposed to untrusted networks or untrusted local users.** Two properties
of the design make this non-negotiable:

### 1. Authentication is optional and off by default

By default there is no login. Setting `ODIN_AUTH_USER` and `ODIN_AUTH_PASSWORD` enables HTTP
Basic Auth across the whole app — both the frontend (UI + the `/api/` proxy) and the backend
API enforce the same credentials, so it also covers requests that reach the API directly
(e.g. another container on the Compose network). With auth disabled, any request that reaches
the app can register and delete metadata, change settings, and launch or cancel pipeline runs
— so enable it whenever the machine is reachable by anyone else.

### 2. The application has host Docker access

To run Nextflow pipelines, the API container mounts the **host Docker socket**
(`/var/run/docker.sock`) and launches pipeline tool containers as siblings on the host
daemon (the "Docker-outside-of-Docker" pattern). Anything that can drive that socket can
create a container that mounts the host filesystem — which is equivalent to **root on the
host**.

Combined, these mean that network access to the API is effectively root access to the
host. Treat reachability of the API port as a full trust boundary.

### How the default deployment contains this

The provided `docker-compose.yml` is configured for single-machine use, but read the
exposure carefully:

- The backend API port (`8080`) is bound to **loopback only** (`127.0.0.1`), so the API
  cannot be reached **directly** from other hosts.
- However, the frontend container **is** published on port `80` (all interfaces), and its
  nginx proxies `/api/` to the backend. So unless you enable authentication, anyone who can
  reach port `80` — including other machines on the LAN — can drive the API through that
  proxy without a login.

To keep the deployment safe, do **at least one** of the following:

- **Enable HTTP Basic Auth** by setting `ODIN_AUTH_USER` and `ODIN_AUTH_PASSWORD` in `.env`
  (recommended — it protects the UI and the `/api/` proxy together).
- **Bind the frontend to loopback** by changing its port mapping to `127.0.0.1:80:80`, so
  only this machine can reach it.

If you deploy differently, you are responsible for preserving these properties.

### Recommendations for operators

- **Enable HTTP Basic Auth** (`ODIN_AUTH_USER` / `ODIN_AUTH_PASSWORD`) whenever the machine
  is reachable by anyone else. Basic Auth sends the credential in effectively cleartext, so
  also terminate TLS in front if you expose ODIN beyond loopback or a trusted LAN.
- **Keep exposure minimal** — bind the frontend (and the API) to loopback or a trusted LAN,
  and do not publish either to an untrusted network.
- **Do not run ODIN on a shared or multi-user machine** where untrusted local users have
  accounts — a local user who can reach the app has the same power as a remote one.
- **To reduce (not eliminate) the Docker-socket blast radius**, consider a filtered socket
  proxy (e.g. Tecnativa `docker-socket-proxy`) allowing only the container lifecycle calls
  Nextflow needs. Because such a proxy filters by API endpoint and not by request body, it
  cannot prevent a permitted `create container` call from bind-mounting the host — so it is
  hardening, not a fix. Stronger isolation (rootless Docker, or a dedicated Docker daemon in
  a VM) is the only way to fully close the host-escape path for this workload.

## Known limitations

These are accepted trade-offs of the current single-machine design, not undisclosed bugs:

- Authentication is optional and off by default; when enabled (`ODIN_AUTH_USER` /
  `ODIN_AUTH_PASSWORD`), both the frontend and the backend API enforce HTTP Basic Auth.
- Host Docker socket access (host-root-equivalent for anything that reaches the API).
- Intended for trusted, single-operator deployments only.

If your environment needs multi-user or network-exposed operation, address these before
deploying (enable authentication, add TLS, and restrict Docker access).
