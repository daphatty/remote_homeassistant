"""Config flow for Remote Home-Assistant integration."""
import re
import logging

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_VERIFY_SSL,
    CONF_ACCESS_TOKEN,
    CONF_ENTITY_ID,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_INCLUDE,
    CONF_EXCLUDE,
    CONF_ABOVE,
    CONF_BELOW,
)
from homeassistant.core import callback

from .rest_api import ApiProblem, CannotConnect, InvalidAuth, async_get_discovery_info
from .const import (
    CONF_REMOTE_CONNECTION,
    CONF_SECURE,
    CONF_FILTER,
    CONF_SUBSCRIBE_EVENTS,
    CONF_ENTITY_PREFIX,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    DOMAIN,
)  # pylint:disable=unused-import

_LOGGER = logging.getLogger(__name__)

ADD_NEW_EVENT = "add_new_event"

FILTER_OPTIONS = [CONF_ENTITY_ID, CONF_UNIT_OF_MEASUREMENT, CONF_ABOVE, CONF_BELOW]

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=8123): int,
        vol.Required(CONF_ACCESS_TOKEN): str,
        vol.Optional(CONF_SECURE, default=True): bool,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


def _filter_str(index, filter):
    entity_id = filter[CONF_ENTITY_ID]
    unit = filter[CONF_UNIT_OF_MEASUREMENT]
    above = filter[CONF_ABOVE]
    below = filter[CONF_BELOW]
    return f"{index+1}. {entity_id}, unit: {unit}, above: {above}, below: {below}"


async def validate_input(hass: core.HomeAssistant, conf):
    """Validate the user input allows us to connect."""
    try:
        info = await async_get_discovery_info(
            hass,
            conf[CONF_HOST],
            conf[CONF_PORT],
            conf[CONF_SECURE],
            conf[CONF_ACCESS_TOKEN],
            conf[CONF_VERIFY_SSL],
        )
    except OSError:
        raise CannotConnect()

    return {"title": info["location_name"], "uuid": info["uuid"]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Remote Home-Assistant."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except ApiProblem:
                errors["base"] = "api_problem"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["uuid"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the Home Assistant remote integration."""

    def __init__(self, config_entry):
        """Initialize localtuya options flow."""
        self.config_entry = config_entry
        self.filters = None
        self.events = None
        self.options = None

    async def async_step_init(self, user_input=None):
        """Manage basic options."""
        if user_input is not None:
            self.options = user_input.copy()
            return await self.async_step_domain_entity_filters()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENTITY_PREFIX,
                        description={
                            "suggested_value": self.config_entry.options.get(
                                CONF_ENTITY_PREFIX
                            )
                        },
                    ): str
                }
            ),
        )

    async def async_step_domain_entity_filters(self, user_input=None):
        """Manage domain and entity filters."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_general_filters()

        domains, entities = self._domains_and_entities()
        return self.async_show_form(
            step_id="domain_entity_filters",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_INCLUDE_DOMAINS,
                        default=self._default(CONF_INCLUDE_DOMAINS),
                    ): cv.multi_select(domains),
                    vol.Optional(
                        CONF_INCLUDE_ENTITIES,
                        default=self._default(CONF_INCLUDE_ENTITIES),
                    ): cv.multi_select(entities),
                    vol.Optional(
                        CONF_EXCLUDE_DOMAINS,
                        default=self._default(CONF_EXCLUDE_DOMAINS),
                    ): cv.multi_select(domains),
                    vol.Optional(
                        CONF_EXCLUDE_ENTITIES,
                        default=self._default(CONF_EXCLUDE_ENTITIES),
                    ): cv.multi_select(entities),
                }
            ),
        )

    async def async_step_general_filters(self, user_input=None):
        """Manage domain and entity filters."""
        if user_input is not None:
            # Continue to next step if entity id is not specified
            if CONF_ENTITY_ID not in user_input:
                # Each filter string is prefixed with a number (index in self.filter+1).
                # Extract all of them and build the final filter list.
                selected_indices = [
                    int(filter.split(".")[0]) - 1
                    for filter in user_input.get(CONF_FILTER, [])
                ]
                self.options[CONF_FILTER] = [self.filters[i] for i in selected_indices]
                return await self.async_step_events()

            selected = user_input.get(CONF_FILTER, [])
            new_filter = {conf: user_input.get(conf) for conf in FILTER_OPTIONS}
            selected.append(_filter_str(len(self.filters), new_filter))
            self.filters.append(new_filter)
        else:
            self.filters = self.config_entry.options.get(CONF_FILTER, [])
            selected = [_filter_str(i, filter) for i, filter in enumerate(self.filters)]

        strings = [_filter_str(i, filter) for i, filter in enumerate(self.filters)]
        return self.async_show_form(
            step_id="general_filters",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_FILTER, default=selected): cv.multi_select(
                        strings
                    ),
                    vol.Optional(CONF_ENTITY_ID): str,
                    vol.Optional(CONF_UNIT_OF_MEASUREMENT): str,
                    vol.Optional(CONF_ABOVE): vol.Coerce(float),
                    vol.Optional(CONF_BELOW): vol.Coerce(float),
                }
            ),
        )

    async def async_step_events(self, user_input=None):
        """Manage event options."""
        if user_input is not None:
            if ADD_NEW_EVENT not in user_input:
                self.options[CONF_SUBSCRIBE_EVENTS] = user_input.get(
                    CONF_SUBSCRIBE_EVENTS, []
                )
                return self.async_create_entry(title="", data=self.options)

            selected = user_input.get(CONF_SUBSCRIBE_EVENTS, [])
            self.events.add(user_input[ADD_NEW_EVENT])
            selected.append(user_input[ADD_NEW_EVENT])
        else:
            self.events = set(
                self.config_entry.options.get(CONF_SUBSCRIBE_EVENTS) or []
            )
            selected = self._default(CONF_SUBSCRIBE_EVENTS)

        return self.async_show_form(
            step_id="events",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SUBSCRIBE_EVENTS, default=selected
                    ): cv.multi_select(self.events),
                    vol.Optional(ADD_NEW_EVENT): str,
                }
            ),
        )

    def _default(self, conf):
        """Return default value for an option."""
        return self.config_entry.options.get(conf) or vol.UNDEFINED

    def _domains_and_entities(self):
        """Return all entities and domains exposed by remote instance."""
        remote = self.hass.data[DOMAIN][self.config_entry.entry_id][
            CONF_REMOTE_CONNECTION
        ]

        # Include entities we have in the config explicitly, otherwise they will be
        # pre-selected and not possible to remove if they are no lobger present on
        # the remote host.
        include_entities = set(self.config_entry.options.get(CONF_INCLUDE_ENTITIES, []))
        exclude_entities = set(self.config_entry.options.get(CONF_EXCLUDE_ENTITIES, []))
        entities = sorted(
            remote._all_entity_names | include_entities | exclude_entities
        )
        domains = sorted(set([entity_id.split(".")[0] for entity_id in entities]))
        return domains, entities
