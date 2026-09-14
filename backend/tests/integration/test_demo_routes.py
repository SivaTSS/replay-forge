"""Only the servicing workstation is deployed; retired demos are not aliases."""

from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "path,final",
    [
        ("/", "/harbor/servicing"),
        ("/harbor", "/harbor/servicing"),
        ("/summit", "/summit/servicing"),
    ],
)
def test_demo_entry_routes_reach_the_single_workstation(
    demo_bank: str, path: str, final: str
) -> None:
    with urlopen(f"{demo_bank}{path}", timeout=5) as response:
        assert response.status == 200
        assert response.url == f"{demo_bank}{final}"


@pytest.mark.parametrize(
    "path",
    [
        "/harbor/member-search",
        "/harbor/member-results",
        "/harbor/accounts/12345/details",
        "/harbor/visual-terminal",
        "/summit/visual-workbench",
        "/unknown",
        "/unknown/servicing",
    ],
)
def test_retired_and_unknown_routes_do_not_deploy_an_alternative_ui(
    demo_bank: str, path: str
) -> None:
    with pytest.raises(HTTPError) as error:
        urlopen(f"{demo_bank}{path}", timeout=5)
    assert error.value.code == 404
