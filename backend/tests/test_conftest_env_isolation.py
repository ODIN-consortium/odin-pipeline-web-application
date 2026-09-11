"""The test suite must not inherit settings overrides from the developer's .env.

``app.main`` calls ``load_dotenv()``, so importing the backend pulls the real ``.env``
into the test process, and an env override beats a DB value by design. Any test that
writes a setting into the DB and asserts on it is therefore only correct while the
person running the suite has no such variable set.

``conftest`` neutralises those variables before the backend is imported. These tests
keep that list honest and prove the mechanism works, so the suite's result does not
depend on whose machine it runs on.
"""

import os

from backend.app.settings_resolver import (
    _ENV_VAR_KEYS,
    get_env_overrides,
    resolve_setting_value,
)

from .conftest import NEUTRALISED_ENV_VARS


def test_every_env_override_key_is_neutralised() -> None:
    """Drift guard: adding a new env-var override must also neutralise it in conftest.

    Without this, a new key silently reintroduces the dependency on local .env
    contents, and the resulting failures look like real bugs on someone else's machine.
    """
    missing = set(_ENV_VAR_KEYS) - set(NEUTRALISED_ENV_VARS)
    assert not missing, (
        f"settings_resolver._ENV_VAR_KEYS gained {sorted(missing)} without being blanked "
        "in conftest.NEUTRALISED_ENV_VARS — see the comment there for why that matters."
    )


def test_neutralised_list_has_no_stale_entries() -> None:
    """The reverse direction: a removed override key should not linger in the list."""
    stale = set(NEUTRALISED_ENV_VARS) - set(_ENV_VAR_KEYS)
    assert not stale, f"NEUTRALISED_ENV_VARS lists non-override vars: {sorted(stale)}"


def test_no_env_overrides_are_active_during_tests() -> None:
    """The mechanism actually works: no setting resolves from the environment."""
    assert get_env_overrides() == {}


def test_blank_env_var_reads_as_unset() -> None:
    """conftest relies on '' meaning "not configured" rather than "set to empty"."""
    for var in NEUTRALISED_ENV_VARS:
        assert os.environ.get(var) == "", f"{var} was not blanked"
    assert get_env_overrides() == {}


def test_a_db_value_is_visible_when_no_env_override_shadows_it(db) -> None:
    """The property the blanking exists to protect.

    With an ambient ODIN_PATHOGENS_FILE this returned the env value instead, which is
    correct behaviour and precisely why it broke tests that configure the DB.
    """
    db.execute(
        "INSERT INTO config_values (id, key, value, updated_at) "
        "VALUES ('iso-1', 'pathogens_file', '/configured/in/db.xlsx', '2026-07-30T00:00:00.000Z')"
    )
    db.commit()
    assert resolve_setting_value(db, "pathogens_file") == "/configured/in/db.xlsx"


def test_an_env_override_still_wins_when_one_is_set(db, monkeypatch) -> None:
    """Blanking must not weaken env precedence — only stop it leaking in ambiently."""
    db.execute(
        "INSERT INTO config_values (id, key, value, updated_at) "
        "VALUES ('iso-2', 'pathogens_file', '/configured/in/db.xlsx', '2026-07-30T00:00:00.000Z')"
    )
    db.commit()
    monkeypatch.setenv("ODIN_PATHOGENS_FILE", "/forced/by/env.xlsx")
    assert resolve_setting_value(db, "pathogens_file") == "/forced/by/env.xlsx"
