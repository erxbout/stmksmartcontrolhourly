import ast
from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
import operator

import async_timeout
import requests

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity, StateType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_operations = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def _safe_eval(node, variables, functions):
    if isinstance(node, ast.Num):
        return node.n
    elif isinstance(node, ast.Name):
        return variables[node.id]  # KeyError -> Unsafe variable
    elif isinstance(node, ast.BinOp):
        op = _operations[node.op.__class__]  # KeyError -> Unsafe operation
        left = _safe_eval(node.left, variables, functions)
        right = _safe_eval(node.right, variables, functions)
        if isinstance(node.op, ast.Pow):
            assert right < 100
        return op(left, right)
    elif isinstance(node, ast.Call):
        assert not node.keywords
        assert isinstance(node.func, ast.Name), "Unsafe function derivation"
        func = functions[node.func.id]  # KeyError -> Unsafe function
        args = [_safe_eval(arg, variables, functions) for arg in node.args]
        return func(*args)

    assert False, "Unsafe operation"


def safe_eval(expr, variables={}, functions={}):
    node = ast.parse(expr, "<string>", "eval").body
    return _safe_eval(node, variables, functions)


_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1
# SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
#     SensorEntityDescription(
#         key=KEY_PVPC,
#         icon="mdi:currency-eur",
#         native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
#         state_class=SensorStateClass.MEASUREMENT,
#         suggested_display_precision=5,
#         name="Awattar",
#     ),
#     SensorEntityDescription(
#         key=KEY_INJECTION,
#         icon="mdi:transmission-tower-export",
#         native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
#         state_class=SensorStateClass.MEASUREMENT,
#         suggested_display_precision=5,
#         name="Injection Price",
#     ),
#     SensorEntityDescription(
#         key=KEY_MAG,
#         icon="mdi:bank-transfer",
#         native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
#         state_class=SensorStateClass.MEASUREMENT,
#         suggested_display_precision=5,
#         name="MAG tax",
#         entity_registry_enabled_default=False,
#     ),
#     SensorEntityDescription(
#         key=KEY_OMIE,
#         icon="mdi:shopping",
#         native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
#         state_class=SensorStateClass.MEASUREMENT,
#         suggested_display_precision=5,
#         name="OMIE Price",
#         entity_registry_enabled_default=False,
#     ),
# )

defaultApiEndpoint = "https://api.awattar.at/v1/marketdata"
_PRICE_SENSOR_ATTRIBUTES_MAP = {
    "data_id": "1003",
    "name": "stmksmartcontrolhourly",
    "tariff": "hourly",
    "period": "period",
    "available_power": "available_power",
    "next_period": "next_period",
    "hours_to_next_period": "1",
    "next_better_price": "next_better_price",
    "hours_to_better_price": "hours_to_better_price",
    "num_better_prices_ahead": "num_better_prices_ahead",
    "price_position": "price_position",
    "price_ratio": "price_ratio",
    "max_price": "max_price",
    "max_price_at": "max_price_at",
    "min_price": "min_price",
    "min_price_at": "min_price_at",
    "next_best_at": "next_best_at",
    "price_00h": "price_00h",
    "price_01h": "price_01h",
    "price_02h": "price_02h",
    "price_03h": "price_03h",
    "price_04h": "price_04h",
    "price_05h": "price_05h",
    "price_06h": "price_06h",
    "price_07h": "price_07h",
    "price_08h": "price_08h",
    "price_09h": "price_09h",
    "price_10h": "price_10h",
    "price_11h": "price_11h",
    "price_12h": "price_12h",
    "price_13h": "price_13h",
    "price_14h": "price_14h",
    "price_15h": "price_15h",
    "price_16h": "price_16h",
    "price_17h": "price_17h",
    "price_18h": "price_18h",
    "price_19h": "price_19h",
    "price_20h": "price_20h",
    "price_21h": "price_21h",
    "price_22h": "price_22h",
    "price_23h": "price_23h",
    # only seen in the evening
    "next_better_price (next day)": "next_better_price (next day)",
    "hours_to_better_price (next day)": "hours_to_better_price (next day)",
    "num_better_prices_ahead (next day)": "num_better_prices_ahead (next day)",
    "price_position (next day)": "price_position (next day)",
    "price_ratio (next day)": "price_ratio (next day)",
    "max_price (next day)": "max_price (next day)",
    "max_price_at (next day)": "max_price_at (next day)",
    "min_price (next day)": "min_price (next day)",
    "min_price_at (next day)": "min_price_at (next day)",
    "next_best_at (next day)": "next_best_at (next day)",
    "price_next_day_00h": "price_next_day_00h",
    "price_next_day_01h": "price_next_day_01h",
    "price_next_day_02h": "price_next_day_02h",
    "price_next_day_03h": "price_next_day_03h",
    "price_next_day_04h": "price_next_day_04h",
    "price_next_day_05h": "price_next_day_05h",
    "price_next_day_06h": "price_next_day_06h",
    "price_next_day_07h": "price_next_day_07h",
    "price_next_day_08h": "price_next_day_08h",
    "price_next_day_09h": "price_next_day_09h",
    "price_next_day_10h": "price_next_day_10h",
    "price_next_day_11h": "price_next_day_11h",
    "price_next_day_12h": "price_next_day_12h",
    "price_next_day_13h": "price_next_day_13h",
    "price_next_day_14h": "price_next_day_14h",
    "price_next_day_15h": "price_next_day_15h",
    "price_next_day_16h": "price_next_day_16h",
    "price_next_day_17h": "price_next_day_17h",
    "price_next_day_18h": "price_next_day_18h",
    "price_next_day_19h": "price_next_day_19h",
    "price_next_day_20h": "price_next_day_20h",
    "price_next_day_21h": "price_next_day_21h",
    "price_next_day_22h": "price_next_day_22h",
    "price_next_day_23h": "price_next_day_23h",
}


async def async_setup_platform(
    hass: HomeAssistant, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(EnergyProductionSensor(), True)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    apiEndpointDE = defaultApiEndpoint.replace(".at", ".de")

    if config_entry.data["SourceCountries"] == "DE":
        coordinator = MyCoordinator(
            hass,
            apiEndpointDE,
            config_entry.data["PollingInterval"],
            config_entry.data["Formula"],
        )
    else:
        coordinator = MyCoordinator(
            hass,
            defaultApiEndpoint,
            config_entry.data["PollingInterval"],
            config_entry.data["Formula"],
        )

    await coordinator.async_config_entry_first_refresh()

    entities = []

    entities.append(AwattarSensor(coordinator))
    async_add_entities(entities)


class MyCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass, apiEndpoint, pollingInterval, formula):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            # Name of the data. For logging purposes.
            _LOGGER,
            name="My sensor",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(minutes=pollingInterval),
        )
        self.apiEndpoint = apiEndpoint
        self.formula = formula
        self.hass = hass

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                # Grab active context variables to limit data required to be fetched from API
                # Note: using context is not required if there is no need or ability to limit
                # data retrieved from API.
                listening_idx = set(self.async_contexts())
                return await self.hass.async_add_executor_job(self.update)
        # except ApiAuthError as err:
        # Raising ConfigEntryAuthFailed will cancel future updates
        # and start a config flow with SOURCE_REAUTH (async_step_reauth)
        # raise ConfigEntryAuthFailed from err
        # except ApiError as err:
        # raise UpdateFailed(f"Error communicating with API: {err}")
        finally:
            print("finally hi")

    def update(self) -> None:
        startOfToday = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        endOfTomorrow = datetime.combine(
            startOfToday + timedelta(days=2), datetime.min.time()
        )

        def fetch_data(api_url, start_timestamp, end_timestamp):
            params = {
                "start": start_timestamp,
                "end": end_timestamp,
            }

            try:
                response = requests.get(api_url, params=params)
                # Check if the request was successful (status code 200)
                if response.status_code == 200:
                    # Parse the JSON data
                    json_data = response.json()
                    return json_data
                else:
                    print(f"Failed to fetch data. Status code: {response.status_code}")
                    return None
            except Exception as e:
                print(f"An error occurred: {e}")
                return None

        data = fetch_data(
            self.apiEndpoint,
            startOfToday.timestamp() * 1000,
            endOfTomorrow.timestamp() * 1000,
        )

        return data


class AwattarSensor(CoordinatorEntity, SensorEntity):
    """An entity using CoordinatorEntity.

    The CoordinatorEntity class provides:
      should_poll
      async_update
      async_added_to_hass
      available

    """

    timestamp: StateType | date | datetime | Decimal = None

    def __init__(self, coordinator):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator, context="awattar")
        self._name = "stmksmartcontrolhourly"
        self._state = 0
        self.formula = coordinator.formula

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data

        print("Fetched data:", data)

        currentKeyPrefix = "price_next_day_"
        currentKey = "price_next_day_00h"

        for i in range(24):
            currentKey = currentKeyPrefix + f"{i:02}" + "h"
            # reset tomorrow data because if its filled from yesterday it does not get filled with new data (or in other words stays filled with same data)
            _PRICE_SENSOR_ATTRIBUTES_MAP[currentKey] = currentKey

        # Convert each timestamp and print the results
        currentKeyPrefix = "price_"
        currentKey = "price_00h"
        endKeyToday = "price_23h"
        endKeyTomorrow = "price_next_day_23h"
        tomorrowKeyPrefix = "price_next_day_"
        for dat in data["data"]:
            converted_timestamp = datetime.fromtimestamp(dat["start_timestamp"] / 1000)
            converted_endtimestamp = datetime.fromtimestamp(dat["end_timestamp"] / 1000)
            # converted_price = round(dat["marketprice"] / 10, 3) * 1.2 + 1.44
            inputVariables = {
                "marketprice_ct_per_kWh": dat["marketprice"] / 10,
                "marketprice_eur_per_kWh": dat["marketprice"] / 1000,
                "marketprice_eur_per_MWh": dat["marketprice"],
            }
            converted_price = safe_eval(
                self.formula,
                inputVariables,
                {"round": round},
            )
            currentKey = currentKeyPrefix + converted_timestamp.strftime("%H") + "h"
            _PRICE_SENSOR_ATTRIBUTES_MAP[currentKey] = converted_price

            if converted_timestamp <= datetime.now() <= converted_endtimestamp:
                self._state = converted_price
                _PRICE_SENSOR_ATTRIBUTES_MAP["period"] = currentKey
                _PRICE_SENSOR_ATTRIBUTES_MAP["next_period"] = (
                    currentKeyPrefix
                    + (converted_timestamp + timedelta(hours=1)).strftime("%H")
                    + "h"
                )

            if currentKey == endKeyToday:
                currentKeyPrefix = tomorrowKeyPrefix

            if currentKey == endKeyTomorrow:
                break

        self.async_write_ha_state()

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the name of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the name of the sensor."""
        result = _PRICE_SENSOR_ATTRIBUTES_MAP.copy()

        if result["price_next_day_00h"] == "price_next_day_00h":
            currentKeyPrefix = "price_next_day_"
            currentKey = "price_next_day_00h"

            for i in range(24):
                currentKey = currentKeyPrefix + f"{i:02}" + "h"
                result.pop(currentKey)

        return result

    @property
    def native_unit_of_measurement(self):
        return "ct/kWh"

    @property
    def suggested_display_precision(self):
        return 2
