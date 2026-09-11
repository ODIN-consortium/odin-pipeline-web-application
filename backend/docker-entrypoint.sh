#!/bin/bash
# Startup checks + runtime CA cert injection — runs before uvicorn starts.
#
# Why this is needed:
#   The image's build-time cert baking only helps the person who built it
#   with their own CA in ca/.  Any other user whose organization also does
#   SSL inspection would still get cert errors from Nextflow's internal
#   JGit client and from curl calls Nextflow makes inside this container
#   (e.g. fetching nfcore_custom.config).
#
#   This script discovers the user's cert the same way odin.config does
#   (ODIN_CA_CERT env var, or auto-scan $ODIN_PIPELINE_ROOT/ca/) and
#   installs it into:
#     1. The system cert store (fixes curl, wget, Python requests)
#     2. Java's cacerts keystore via ca-certificates-java
#        (fixes JGit used by Nextflow for GitHub version checks)
#   Both are achieved by a single update-ca-certificates call because
#   ca-certificates-java hooks into it automatically.
set -e

# ── Required configuration ────────────────────────────────────────────────────
# ODIN_PIPELINE_ROOT has no default: every path setting (minknow_dir, output_dir,
# biomeme_dir, ...) is derived from it, so guessing a value would point the whole
# installation at a directory nobody chose.
#
# docker compose already refuses to start without it — the bind mount
# "${ODIN_PIPELINE_ROOT}:${ODIN_PIPELINE_ROOT}" becomes ":" and the daemon rejects
# it — but that error names a mount path, not the variable. Failing here gives the
# operator a message they can act on, and covers `docker run` too.
if [ -z "${ODIN_PIPELINE_ROOT:-}" ]; then
    echo "[ODIN] FATAL: ODIN_PIPELINE_ROOT is not set." >&2
    echo "[ODIN]   It is required and has no default — every path setting is derived" >&2
    echo "[ODIN]   from it. Set it in .env (see .env.example), e.g.:" >&2
    echo "[ODIN]     ODIN_PIPELINE_ROOT=/mnt/<drive>/ODIN   # WSL2 / Docker on Windows" >&2
    echo "[ODIN]     ODIN_PIPELINE_ROOT=/srv/odin           # Linux" >&2
    echo "[ODIN]   The same path must be bind-mounted identically in docker-compose.yml." >&2
    exit 1
fi

CERT_FILE=""

if [ -n "${ODIN_CA_CERT:-}" ] && [ -f "$ODIN_CA_CERT" ]; then
    CERT_FILE="$ODIN_CA_CERT"
elif [ -n "${ODIN_PIPELINE_ROOT:-}" ]; then
    # -type f: a DIRECTORY with a cert-like name is a classic Docker artifact
    # (a bind mount whose source file did not exist at container create gets a
    # directory manufactured in its place). Installing it is impossible and
    # must not crash-loop the container.
    CERT_FILE=$(find "${ODIN_PIPELINE_ROOT}/ca" -maxdepth 1 -type f \
        \( -name '*.cer' -o -name '*.crt' -o -name '*.pem' \) \
        2>/dev/null | head -1 || true)
fi

if [ -n "$CERT_FILE" ] && [ ! -f "$CERT_FILE" ]; then
    echo "[ODIN] WARNING: CA cert path is not a regular file, skipping: $CERT_FILE" >&2
    CERT_FILE=""
fi

if [ -n "$CERT_FILE" ] && ! cp "$CERT_FILE" /usr/local/share/ca-certificates/odin-runtime-ca.crt 2>/dev/null; then
    # A cert we cannot read/copy must not take the whole backend down —
    # pipelines then fail with clear TLS errors instead, which is diagnosable.
    echo "[ODIN] WARNING: could not install CA cert, continuing without it: $CERT_FILE" >&2
    CERT_FILE=""
fi

if [ -n "$CERT_FILE" ]; then
    echo "[ODIN] Installing runtime CA cert: $CERT_FILE"
    update-ca-certificates 2>&1 | grep -v '^$' || true
    # Export env vars so any process in this container trusts the updated store
    export CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
    export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
    export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
    export NODE_EXTRA_CA_CERTS="$CERT_FILE"
    echo "[ODIN] CA cert installed — system, Java, curl, and Python will trust it."
fi

# ── Seed the bundled taxprofiler workflow onto the host ───────────────────────
# The image carries the pinned nf-core/taxprofiler source (see Dockerfile), but
# Nextflow bind-mounts pipeline paths (bin/) into sibling task containers that
# resolve paths on the HOST — so the checkout must live under
# ODIN_PIPELINE_ROOT to be usable.  Seeded once per version; never overwritten
# (the copy is version-suffixed, so an image upgrade seeds its own copy
# alongside).  Copy to a temp name + rename so a crash mid-copy cannot leave a
# half-seeded directory that would then be trusted.
if [ -n "${ODIN_BUNDLED_TAXPROFILER:-}" ] && [ -d "$ODIN_BUNDLED_TAXPROFILER" ]; then
    seed_target="${ODIN_PIPELINE_ROOT}/nf/$(basename "$ODIN_BUNDLED_TAXPROFILER")"
    if [ ! -d "$seed_target" ]; then
        echo "[ODIN] Seeding bundled taxprofiler workflow to: $seed_target"
        mkdir -p "${ODIN_PIPELINE_ROOT}/nf"
        rm -rf "${seed_target}.seeding"
        if cp -r "$ODIN_BUNDLED_TAXPROFILER" "${seed_target}.seeding" \
           && mv "${seed_target}.seeding" "$seed_target"; then
            # This container runs as root, but the seeded tree lives in the
            # operator's directory — match its ownership (and that of the nf/
            # parent this script may have just created) to the pipeline root
            # so the operator can manage (or delete) their own files.
            chown --reference="$ODIN_PIPELINE_ROOT" "${ODIN_PIPELINE_ROOT}/nf" 2>/dev/null || true
            chown -R --reference="$ODIN_PIPELINE_ROOT" "$seed_target" 2>/dev/null || true
            echo "[ODIN] Workflow seeded — taxprofiler launches need no GitHub access."
        else
            echo "[ODIN] WARNING: seeding failed — taxprofiler will be pulled from GitHub at first launch." >&2
            rm -rf "${seed_target}.seeding"
        fi
    fi
fi

exec "$@"
