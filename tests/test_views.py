"""Friendly view names <-> Hava view class names."""

from __future__ import annotations

import pytest

from hava_mcp.errors import HavaError
from hava_mcp.views import canonical_name, pick_view, short_name

VIEWS = [
    {"id": "v-infra", "type": "Views::Infrastructure"},
    {"id": "v-ext", "type": "Views::Infrastructure::Extended"},
    {"id": "v-list", "type": "Views::List"},
]


@pytest.mark.parametrize(
    "given,expected",
    [
        ("infrastructure", "Views::Infrastructure"),
        ("INFRA", "Views::Infrastructure"),
        ("extended", "Views::Infrastructure::Extended"),
        ("infrastructure-extended", "Views::Infrastructure::Extended"),
        ("container", "Views::Container"),
        ("containers", "Views::Container"),
        ("Views::Security", "Views::Security"),
    ],
)
def test_canonical_name_accepts_aliases(given, expected):
    assert canonical_name(given) == expected


def test_canonical_name_rejects_nonsense_and_lists_options():
    with pytest.raises(HavaError) as info:
        canonical_name("sideways")
    assert "infrastructure" in str(info.value)


def test_short_name_round_trips_and_degrades_gracefully():
    assert short_name("Views::Infrastructure::Extended") == "extended"
    assert short_name("Views::Something::New") == "something_new"
    assert short_name(None) == "unknown"


def test_pick_view_finds_the_requested_view():
    assert pick_view(VIEWS, "extended")["id"] == "v-ext"


def test_pick_view_error_names_the_available_views():
    with pytest.raises(HavaError) as info:
        pick_view(VIEWS, "security")
    message = str(info.value)
    assert "extended" in message and "infrastructure" in message and "list" in message


def test_pick_view_explains_an_empty_view_list():
    with pytest.raises(HavaError) as info:
        pick_view([], "infrastructure")
    assert "still building" in str(info.value)
