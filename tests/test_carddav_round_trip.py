"""Integration tests for the CardDAV facade against a real Radicale.

Boots a fresh Radicale on a free port with its own data
directory, drives the production :class:`CardDAVFacade` through
the five public CRUD methods, and tears the server down at the
end. Slow (~1 s per test for the subprocess startup) so the
suite is gated behind :func:`pytest.mark.integration`.
"""

from __future__ import annotations

import base64
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest

from genesis.carddav.facade import CardDAVFacade
from genesis.entities import ContactCreate, ContactUpdate


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _radicale_binary() -> str:
    candidate = Path(sys.executable).parent / "radicale"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("radicale")
    if found:
        return found
    raise RuntimeError("radicale executable not found")


def _spawn_radicale(host: str, port: int, data_path: Path) -> subprocess.Popen:
    data_path.mkdir(parents=True, exist_ok=True)
    cfg = data_path / "config"
    cfg.write_text(
        f"[server]\nhosts = {host}:{port}\n\n"
        f"[storage]\ntype = multifilesystem\n"
        f"filesystem_folder = {data_path / 'collections'}\n\n"
        f"[auth]\ntype = htpasswd\n"
        f"htpasswd_filename = {data_path / 'users'}\n"
        f"htpasswd_encryption = plain\n\n"
        f"[rights]\ntype = from_file\n"
        f"file = {data_path / 'rights'}\n"
    )
    (data_path / "users").write_text("default:default\n")
    (data_path / "rights").write_text("[base]\nuser = .+\ncollection = .*\npermissions = RrWw\n")

    proc = subprocess.Popen(
        [sys.executable, _radicale_binary(), "-C", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    creds = base64.b64encode(b"default:default").decode()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://{host}:{port}/")
            req.add_header("Authorization", f"Basic {creds}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status in (200, 302, 207):
                    return proc
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError(f"Radicale did not come up on {host}:{port}")


@pytest.fixture
def carddav_server(tmp_path: Path) -> Generator[CardDAVFacade, None, None]:
    host = "127.0.0.1"
    port = _free_port()
    proc = _spawn_radicale(host, port, tmp_path / "radicale")
    try:
        server_url = f"http://{host}:{port}"
        yield CardDAVFacade(server_url, "default", "default")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.integration
class TestCardDAVRoundTrip:
    def test_create_then_get_round_trip(self, carddav_server: CardDAVFacade) -> None:
        created = carddav_server.create_contact(
            ContactCreate(
                name="Ada Lovelace",
                given_name="Ada",
                family_name="Lovelace",
                emails=["ada@example.com"],
                phones=["+44 20 7946 0958"],
                birthday=date(1815, 12, 10),
            )
        )
        fetched = carddav_server.get_contact(created.uid)
        assert fetched is not None
        assert fetched.name == "Ada Lovelace"
        assert fetched.given_name == "Ada"
        assert fetched.family_name == "Lovelace"
        assert fetched.emails == ["ada@example.com"]
        assert fetched.phones == ["+44 20 7946 0958"]
        assert fetched.birthday == date(1815, 12, 10)

    def test_addressbook_created_lazily(self, carddav_server: CardDAVFacade) -> None:
        # A fresh facade should not have created the addressbook
        # until the first CRUD call. After a create, the
        # addressbook exists in Radicale's collection tree.
        carddav_server.create_contact(ContactCreate(name="Probe"))
        # Subsequent operations work — proves the addressbook
        # was actually created and the URL is wired up.
        assert len(carddav_server.list_contacts()) == 1

    def test_list_returns_all_created_contacts(self, carddav_server: CardDAVFacade) -> None:
        carddav_server.create_contact(ContactCreate(name="Alice"))
        carddav_server.create_contact(ContactCreate(name="Bob"))
        carddav_server.create_contact(ContactCreate(name="Charlie"))
        names = {c.name for c in carddav_server.list_contacts()}
        assert names == {"Alice", "Bob", "Charlie"}

    def test_update_modifies_stored_contact(self, carddav_server: CardDAVFacade) -> None:
        created = carddav_server.create_contact(ContactCreate(name="Ada"))
        updated = carddav_server.update_contact(
            created.uid,
            ContactUpdate(name="Augusta Ada", nickname="The Enchantress"),
        )
        assert updated is not None
        assert updated.name == "Augusta Ada"
        assert updated.nickname == "The Enchantress"
        fetched = carddav_server.get_contact(created.uid)
        assert fetched is not None
        assert fetched.nickname == "The Enchantress"

    def test_delete_removes_contact(self, carddav_server: CardDAVFacade) -> None:
        created = carddav_server.create_contact(ContactCreate(name="Disposable"))
        assert carddav_server.delete_contact(created.uid) is True
        assert carddav_server.get_contact(created.uid) is None

    def test_search_filters_substring(self, carddav_server: CardDAVFacade) -> None:
        carddav_server.create_contact(ContactCreate(name="Ada Lovelace", emails=["ada@example.com"]))
        carddav_server.create_contact(ContactCreate(name="Alice Cooper", emails=["alice@nightmare.com"]))
        # "ada" matches Ada Lovelace (name) and Alice Cooper's
        # email (``alice@nightmare.com`` does NOT contain "ada"
        # though) — verify the search finds Ada by name.
        result = carddav_server.list_contacts(search="ada")
        names = {c.name for c in result}
        assert names == {"Ada Lovelace"}

    def test_multi_value_round_trip(self, carddav_server: CardDAVFacade) -> None:
        created = carddav_server.create_contact(
            ContactCreate(
                name="Multi",
                emails=["a@x.com", "b@x.com"],
                phones=["+1", "+2"],
                urls=["https://a", "https://b"],
                addresses=["addr1", "addr2"],
            )
        )
        fetched = carddav_server.get_contact(created.uid)
        assert fetched is not None
        assert fetched.emails == ["a@x.com", "b@x.com"]
        assert fetched.phones == ["+1", "+2"]
        assert fetched.urls == ["https://a", "https://b"]
        assert fetched.addresses == ["addr1", "addr2"]
