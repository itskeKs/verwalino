"""Steps every feature may use: who is signed in."""

from __future__ import annotations

from pytest_bdd import given, parsers


@given(parsers.parse('an administrator of "{organization}" is signed in'))
def administrator_is_signed_in(world, organization):
    world.actor = world.administrator_of(organization)
    world.actor_organization = organization
