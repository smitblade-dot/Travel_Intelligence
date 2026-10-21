"""Interface for future controlled publication. Deliberately disabled and unimplemented."""
from __future__ import annotations


class PublicationDisabled(Exception):
    pass


class PublicationGateway:
    """Any future implementation must require: verified claim, approval record, human or policy sign-off.

    This build ships no implementation. publish() always refuses, whatever the configuration says.
    """
    def __init__(self, cfg):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return False

    def publish(self, record: dict):
        raise PublicationDisabled("Automatic publication is disabled in this build; no publisher is implemented")
