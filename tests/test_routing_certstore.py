"""The certificate-store backend and the installer (phase 3b.2). Only the in-memory fake is used: the real
``CurrentUser\\Root`` backend cannot be built under test (``test_routing_guard.py``)."""

from __future__ import annotations

import json

import pytest

from client_gateway.routing.ca import build_ca
from client_gateway.routing.certstore import (
    FRIENDLY_NAME,
    CAInstaller,
    CertStoreError,
    MemoryCertStore,
)


@pytest.fixture(scope="module")
def ca():
    return build_ca()


@pytest.fixture(scope="module")
def other():
    return build_ca()


@pytest.fixture()
def backend():
    return MemoryCertStore()


@pytest.fixture()
def installer(tmp_path, backend):
    return CAInstaller(tmp_path, backend)


# ---------------------------------------------------------------------------------- the fake
def test_the_fake_has_the_interface_and_nothing_leaves_the_object(backend, ca):
    assert not backend.installed(ca.thumbprint) and backend.list_ours() == []
    backend.install(ca.der)
    assert backend.installed(ca.thumbprint) and backend.installed(ca.thumbprint.lower())
    assert backend.list_ours() == [ca.thumbprint]
    backend.remove(ca.thumbprint)
    assert not backend.installed(ca.thumbprint)
    with pytest.raises(CertStoreError, match="not_installed"):
        backend.remove(ca.thumbprint)


def test_a_user_who_declines_windows_dialog_is_a_fixed_code(ca):
    with pytest.raises(CertStoreError, match="user_declined"):
        MemoryCertStore(decline=True).install(ca.der)


# ---------------------------------------------------------------------------------- the installer
def test_install_records_the_thumbprint_in_state_json(installer, backend, ca, tmp_path):
    result = installer.install(ca)
    assert result == {"installed": True, "changed": True, "thumbprint": ca.thumbprint}
    assert backend.installed(ca.thumbprint) and installer.recorded() == [ca.thumbprint]
    state = json.loads((tmp_path / "routing" / "state.json").read_text(encoding="utf-8"))
    assert state["ca_thumbprints"] == [ca.thumbprint]
    again = installer.install(ca)  # idempotent: the store is not touched a second time
    assert again["changed"] is False and backend.calls == [("install", ca.thumbprint)]


def test_a_declined_install_records_nothing(tmp_path, ca):
    installer = CAInstaller(tmp_path, MemoryCertStore(decline=True))
    with pytest.raises(CertStoreError, match="user_declined"):
        installer.install(ca)
    assert installer.recorded() == [] and not (tmp_path / "routing" / "state.json").exists()


def test_a_store_that_does_not_show_the_certificate_afterwards_is_a_failure(tmp_path, ca):
    class Lying(MemoryCertStore):
        def install(self, der: bytes) -> None:
            self.calls.append(("install", ""))  # accepted, but nothing is in the store

    installer = CAInstaller(tmp_path, Lying())
    with pytest.raises(CertStoreError, match="install_failed"):
        installer.install(ca)
    assert installer.recorded() == []


def test_remove_takes_out_what_was_installed_and_clears_the_record(installer, backend, ca):
    installer.install(ca)
    assert installer.remove() == {"removed": [ca.thumbprint], "skipped": []}
    assert backend.certs == {} and installer.recorded() == []


def test_a_certificate_somebody_else_put_in_the_store_is_never_removed(
    installer, backend, ca, other
):
    foreign = backend.add_foreign(other.der, "Some corporate root")
    installer.install(ca)
    installer.remove()
    assert backend.installed(foreign) and ("remove", foreign) not in backend.calls
    assert installer.remove(foreign) == {"removed": [], "skipped": [foreign]}
    assert backend.installed(foreign)


def test_a_certificate_with_our_name_that_was_not_recorded_is_left_alone(installer, backend, other):
    stray = backend.add_foreign(
        other.der, FRIENDLY_NAME
    )  # same friendly name, never installed by us
    assert installer.remove() == {"removed": [], "skipped": []}
    assert installer.remove(stray) == {"removed": [], "skipped": [stray]}
    assert backend.installed(stray) and ("remove", stray) not in backend.calls
    assert installer.status()["unrecorded"] == [stray]


def test_a_recorded_certificate_that_lost_its_name_is_not_ours_any_more(installer, backend, ca):
    installer.install(ca)
    backend.certs[ca.thumbprint] = (ca.der, "renamed by an admin")
    assert installer.remove() == {"removed": [], "skipped": [ca.thumbprint]}
    assert backend.installed(ca.thumbprint)


def test_replace_trusts_the_new_ca_before_the_old_one_goes(installer, backend, ca, other):
    installer.install(ca)
    result = installer.replace(ca.thumbprint, other)
    assert result["changed"] is True and result["old"]["removed"] == [ca.thumbprint]
    assert [op for op, _ in backend.calls] == ["install", "install", "remove"]
    assert backend.list_ours() == [other.thumbprint] and installer.recorded() == [other.thumbprint]
    assert installer.replace(None, other)["changed"] is False
    assert installer.replace(other.thumbprint, other).get("old") is None  # never remove the new CA


def test_status_lists_recorded_and_in_store_certificates(installer, ca, other):
    assert installer.status(ca) == {
        "recorded": [],
        "in_store": [],
        "unrecorded": [],
        "current_trusted": False,
    }
    installer.install(ca)
    status = installer.status(ca)
    assert status["recorded"] == [ca.thumbprint] == status["in_store"]
    assert status["current_trusted"] is True and installer.status(other)["current_trusted"] is False


def test_a_damaged_state_file_reads_as_nothing_recorded(installer, tmp_path, backend, ca):
    installer.install(ca)
    (tmp_path / "routing" / "state.json").write_text("not json", encoding="utf-8")
    assert installer.recorded() == []
    assert installer.remove() == {"removed": [], "skipped": []}  # never guesses what is ours
    assert backend.installed(ca.thumbprint)
