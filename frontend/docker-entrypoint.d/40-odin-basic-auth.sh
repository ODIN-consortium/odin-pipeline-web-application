#!/bin/sh
# Configure optional HTTP Basic Auth for the ODIN frontend at container startup.
#
# Basic Auth protects the whole site (static UI, the /api/ reverse proxy, and the
# SSE log stream), because the browser replays the cached credential on every
# same-origin request. It is enabled only when BOTH ODIN_AUTH_USER and
# ODIN_AUTH_PASSWORD are set; otherwise the app runs unauthenticated (see
# SECURITY.md). The official nginx image runs this script before starting nginx.
#
# NOTE: Basic Auth sends the credential base64-encoded (effectively cleartext) on
# every request. Over loopback or a trusted LAN this is fine; if you expose the
# app to an untrusted network, terminate TLS in front of it as well.
set -e

SNIPPET=/etc/nginx/auth_snippet.conf
HTPASSWD=/etc/nginx/.htpasswd

if [ -n "$ODIN_AUTH_USER" ] && [ -n "$ODIN_AUTH_PASSWORD" ]; then
    # -c create, -b batch, -m MD5 (apr1 — supported by nginx on every platform)
    htpasswd -cbm "$HTPASSWD" "$ODIN_AUTH_USER" "$ODIN_AUTH_PASSWORD" >/dev/null
    cat > "$SNIPPET" <<'EOF'
auth_basic "ODIN — authorized access only";
auth_basic_user_file /etc/nginx/.htpasswd;
EOF
    echo "odin: HTTP Basic Auth ENABLED (user '$ODIN_AUTH_USER')."
else
    # No credentials configured — leave auth disabled (empty snippet).
    : > "$SNIPPET"
    echo "odin: WARNING — HTTP Basic Auth is DISABLED. Set ODIN_AUTH_USER and" >&2
    echo "odin: ODIN_AUTH_PASSWORD in .env to require a login. The UI and API are" >&2
    echo "odin: otherwise reachable without authentication — see SECURITY.md." >&2
fi
