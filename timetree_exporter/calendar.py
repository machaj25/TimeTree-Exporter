"""TimeTree calendar domain object."""

from dataclasses import dataclass
from typing import Protocol


class CalendarApi(Protocol):
    """API methods needed by a selected calendar."""

    def get_events(
        self,
        calendar_id,
        calendar_name,
        calendar_users=None,
        include_comments=False,
        num_workers=10,
        include_images=False,
    ):
        """Return events for a calendar."""

    def get_events_since(
        self,
        calendar_id,
        since,
        calendar_name=None,
        calendar_users=None,
        include_comments=False,
        num_workers=10,
        include_images=False,
    ):
        """Return an EventPage of events changed since a sync cursor."""

    def get_public_events(self, calendar_id, calendar_name):
        """Return events for a public calendar."""

    def get_public_labels(self, calendar_id):
        """Return labels for a public calendar."""

    def download_image(self, object_key, output_path):
        """Download a private event image."""

    def get_labels(self, calendar_id):
        """Return labels for a calendar."""


@dataclass(frozen=True)
class Calendar:
    """TimeTree calendar Object."""

    api: CalendarApi
    metadata: dict

    @property
    def id(self):
        """Return the calendar id."""
        return self.metadata["id"]

    @property
    def name(self):
        """Return the calendar name."""
        return self.metadata["name"]

    @property
    def alias_code(self):
        """Return the calendar alias code."""
        return self.metadata.get("alias_code")

    @property
    def is_public(self):
        """Return whether this calendar should use the public calendar API."""
        return self.metadata.get("public", False)

    @property
    def calendar_users(self):
        """Return the raw calendar member list from the metadata."""
        return self.metadata.get("calendar_users") or []

    def user_names(self) -> dict:
        """Return calendar member display names keyed by user id."""
        names = {}
        for user in self.calendar_users:
            if not isinstance(user, dict):
                continue
            user_id = user.get("user_id") or user.get("id")
            name = user.get("name")
            if user_id is not None and name:
                names[user_id] = name
        return names

    @property
    def supports_incremental_sync(self):
        """Return whether the API can fetch changes since a sync cursor."""
        return not self.is_public and callable(getattr(self.api, "get_events_since", None))

    def get_events(self, include_comments=False, num_workers=10, include_images=False):
        """Return events for this calendar."""
        if self.is_public:
            return self.api.get_public_events(self.id, self.name)
        return self.api.get_events(
            self.id,
            self.name,
            self.metadata.get("calendar_users"),
            include_comments=include_comments,
            include_images=include_images,
            num_workers=num_workers,
        )

    def get_events_since(self, since, include_comments=False, num_workers=10, include_images=False):
        """Return an EventPage of changes since ``since`` (None for a full fetch)."""
        if not self.supports_incremental_sync:
            raise ValueError("Incremental sync is only available for private calendars")
        return self.api.get_events_since(
            self.id,
            since,
            calendar_name=self.name,
            calendar_users=self.metadata.get("calendar_users"),
            include_comments=include_comments,
            include_images=include_images,
            num_workers=num_workers,
        )

    def download_image(self, object_key, output_path):
        """Download a private event image through the calendar API."""
        return self.api.download_image(object_key, output_path)

    def get_labels(self):
        """Return labels for this calendar."""
        if self.is_public:
            return self.api.get_public_labels(self.id)
        return self.api.get_labels(self.id)
