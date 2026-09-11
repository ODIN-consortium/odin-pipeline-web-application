"""The inputs to a stored derived code are frozen once that code has been published.

`sites.site_code` and `samples.sample_code` are *stored* derivations, unlike `alias` and
`minknow_sample_id` which are computed on read and so can never drift. Two consequences
follow, and they set the two predicates tested here:

  * Renaming a site rewrites `site_code`, but every sample's stored `sample_code` keeps the
    old value — verified before this guard existed: a site went NOBGOPark -> NOBGOHarbour
    while its sample stayed NOBGOPark_water. So a site is frozen as soon as **any sample**
    references it.
  * Changing a sample's site or type re-derives that sample's own `sample_code`, so the row
    stays self-consistent and nothing goes stale. What changes is its *identity*, which only
    matters once published — so a sample is frozen once it **has sequencing runs**.

Why freezing rather than cascading: `alias` (sample_code + barcode) is written into the
Nextflow samplesheet (pipeline/samplesheet.py), so output directories and reports already on
disk are named from the old code. Rewriting the database would break that correspondence with
nothing able to repair it. `sample_code` is also half the sync merge key
(UNIQUE (sample_code, sampling_date)), so a rename diverges two devices' keys.
"""

import sqlite3

from fastapi.testclient import TestClient

SITE = {"country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "Park"}


def _site(client: TestClient, **overrides) -> dict:
    r = client.post("/api/sites", json={**SITE, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def _sample(client: TestClient, site_id: str, sample_type: str = "water") -> dict:
    r = client.post(
        "/api/samples",
        json={"site_id": site_id, "sample_type": sample_type, "sampling_date": "20240601"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _nanopore_run(client: TestClient, sample_id: str) -> dict:
    r = client.post(
        "/api/nanopore-runs",
        json={
            "run_accession": "ERR123456",
            "barcode": "barcode01",
            "protocol_id": "SQK-LSK114",
            "sequencing_kit_id": "SQK-LSK114",
            "sample_id": sample_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# ── sites: frozen once any sample references the site ─────────────────────────


def test_site_components_are_editable_while_no_sample_references_the_site(
    client: TestClient,
) -> None:
    """A typo fix on an unused site stays allowed — there is no stored copy to go stale."""
    site = _site(client)
    r = client.patch(f"/api/sites/{site['id']}", json={"site": "Beach"})
    assert r.status_code == 200, r.text
    assert r.json()["site_code"] == "NOBGOBeach"


def test_site_component_change_is_rejected_once_a_sample_exists(
    client: TestClient, db: sqlite3.Connection
) -> None:
    site = _site(client)
    _sample(client, site["id"])

    r = client.patch(f"/api/sites/{site['id']}", json={"site": "Harbour"})
    assert r.status_code == 409, r.text
    assert "sample(s) reference it" in r.json()["detail"]

    # And nothing moved: the site keeps its code, so the sample's stays consistent.
    assert client.get(f"/api/sites/{site['id']}").json()["site_code"] == "NOBGOPark"


def test_each_site_code_component_is_frozen(client: TestClient) -> None:
    site = _site(client)
    _sample(client, site["id"])
    for field, value in (("country_code", "SE"), ("city_code", "OSL"), ("site", "Harbour")):
        r = client.patch(f"/api/sites/{site['id']}", json={field: value})
        assert r.status_code == 409, f"{field} should be frozen: {r.text}"
        assert field in r.json()["detail"]


def test_other_site_fields_stay_editable_when_samples_exist(client: TestClient) -> None:
    """The restriction is exactly the three fields that feed site_code, nothing more."""
    site = _site(client)
    _sample(client, site["id"])
    r = client.patch(
        f"/api/sites/{site['id']}",
        json={"city": "Bergen", "location": "By the pond", "comments": "note", "latitude": 60.4},
    )
    assert r.status_code == 200, r.text
    assert r.json()["location"] == "By the pond"
    assert r.json()["site_code"] == "NOBGOPark"


def test_resubmitting_an_unchanged_component_is_not_a_rename(client: TestClient) -> None:
    """The UI submits the whole form, so sending the current value must not be rejected."""
    site = _site(client)
    _sample(client, site["id"])
    r = client.patch(f"/api/sites/{site['id']}", json={**SITE, "location": "unchanged-check"})
    assert r.status_code == 200, r.text


# ── samples: frozen once the sample has sequencing runs ───────────────────────


def test_sample_type_is_editable_while_the_sample_has_no_runs(client: TestClient) -> None:
    """A sample that never reached a samplesheet can still be corrected."""
    site = _site(client)
    sample = _sample(client, site["id"])
    r = client.patch(f"/api/samples/{sample['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 200, r.text
    assert r.json()["sample_code"] == "NOBGOPark_sediment"


def test_sample_type_change_is_rejected_once_a_nanopore_run_exists(
    client: TestClient,
) -> None:
    site = _site(client)
    sample = _sample(client, site["id"])
    _nanopore_run(client, sample["id"])

    r = client.patch(f"/api/samples/{sample['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 409, r.text
    assert "sequencing run(s) reference it" in r.json()["detail"]
    assert client.get(f"/api/samples/{sample['id']}").json()["sample_code"] == "NOBGOPark_water"


def test_sample_site_change_is_rejected_once_a_nanopore_run_exists(
    client: TestClient,
) -> None:
    site = _site(client)
    other = _site(client, site="Beach")
    sample = _sample(client, site["id"])
    _nanopore_run(client, sample["id"])

    r = client.patch(f"/api/samples/{sample['id']}", json={"site_id": other["id"]})
    assert r.status_code == 409, r.text
    assert "site_id" in r.json()["detail"]


def test_other_sample_fields_stay_editable_when_runs_exist(client: TestClient) -> None:
    site = _site(client)
    sample = _sample(client, site["id"])
    _nanopore_run(client, sample["id"])
    r = client.patch(
        f"/api/samples/{sample['id']}", json={"comments": "still editable", "depth": "2m"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["comments"] == "still editable"


def test_resubmitting_an_unchanged_sample_type_is_not_a_change(client: TestClient) -> None:
    site = _site(client)
    sample = _sample(client, site["id"])
    _nanopore_run(client, sample["id"])
    r = client.patch(
        f"/api/samples/{sample['id']}",
        json={"site_id": site["id"], "sample_type": "water", "comments": "unchanged-check"},
    )
    assert r.status_code == 200, r.text


def test_a_biomeme_run_also_freezes_the_sample(client: TestClient) -> None:
    """Biomeme runs reach the same derived identity, so they freeze it too."""
    site = _site(client)
    sample = _sample(client, site["id"])
    r = client.post(
        "/api/biomeme-runs",
        json={"biomeme_run_name": "BM-001", "sample_id": sample["id"]},
    )
    assert r.status_code == 201, r.text

    r = client.patch(f"/api/samples/{sample['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 409, r.text


# ── the two guards interact, and the message must not over-promise ────────────


def test_country_can_be_corrected_when_no_other_site_shares_the_code(
    client: TestClient,
) -> None:
    """`country` does not feed site_code, so the freeze does not apply to it."""
    site = _site(client)
    _sample(client, site["id"])

    r = client.patch(f"/api/sites/{site['id']}", json={"country": "Noreg"})
    assert r.status_code == 200, r.text
    assert r.json()["country"] == "Noreg"
    assert r.json()["site_code"] == "NOBGOPark"


def test_country_cannot_be_changed_away_from_a_code_another_site_shares(
    client: TestClient,
) -> None:
    """A separate guard keeps a code meaning one thing across sites, and it still applies.

    Reported from the running app: the freeze message said country was editable, and it is —
    but `_check_code_conflicts` independently refuses to let 'NO' mean Norway on one site and
    Sweden on another. Both refusals are correct; the message had to stop implying otherwise.
    """
    first = _site(client)
    _sample(client, first["id"])
    _site(client, site="Beach")  # second site, same country_code 'NO'

    r = client.patch(f"/api/sites/{first['id']}", json={"country": "Sweden"})
    assert r.status_code == 409, r.text
    assert "already associated" in r.json()["detail"]


def test_the_freeze_message_does_not_promise_country_is_unconditionally_editable(
    client: TestClient,
) -> None:
    site = _site(client)
    _sample(client, site["id"])

    detail = client.patch(f"/api/sites/{site['id']}", json={"site": "Harbour"}).json()["detail"]

    assert "Location, coordinates and comments remain freely editable" in detail
    assert "must stay consistent with their codes" in detail


def test_location_and_comments_are_genuinely_free_on_a_site_in_use(
    client: TestClient,
) -> None:
    """The part of the message that is an unconditional promise must hold."""
    site = _site(client)
    _sample(client, site["id"])
    _site(client, site="Beach")  # another site sharing the codes

    r = client.patch(
        f"/api/sites/{site['id']}",
        json={"location": "By the pond", "comments": "note", "latitude": 60.4, "longitude": 5.3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["location"] == "By the pond"
