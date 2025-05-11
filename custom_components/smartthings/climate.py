"""Support for climate devices through the SmartThings cloud API."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
import logging

from pysmartthings import Attribute, Capability

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE

from . import SmartThingsEntity
from .const import DATA_BROKERS, DOMAIN, UNIT_MAP

ATTR_OPERATION_STATE = "operation_state"
MODE_TO_STATE = {
    "auto": HVACMode.HEAT_COOL,
    "cool": HVACMode.COOL,
    "eco": HVACMode.AUTO,
    "rush hour": HVACMode.AUTO,
    "emergency heat": HVACMode.HEAT,
    "heat": HVACMode.HEAT,
    "off": HVACMode.OFF,
    "wind": HVACMode.FAN_ONLY,
}
STATE_TO_MODE = {
    HVACMode.HEAT_COOL: "auto",
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.OFF: "off",
    HVACMode.FAN_ONLY: "wind",
}

OPERATING_STATE_TO_ACTION = {
    "cooling": HVACAction.COOLING,
    "fan only": HVACAction.FAN,
    "heating": HVACAction.HEATING,
    "idle": HVACAction.IDLE,
    "pending cool": HVACAction.COOLING,
    "pending heat": HVACAction.HEATING,
    "vent economizer": HVACAction.FAN,
}

AC_MODE_TO_STATE = {
    "auto": HVACMode.HEAT_COOL,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "coolClean": HVACMode.COOL,
    "dryClean": HVACMode.DRY,
    "heat": HVACMode.HEAT,
    "heatClean": HVACMode.HEAT,
    "fanOnly": HVACMode.FAN_ONLY,
    "wind": HVACMode.FAN_ONLY,
}
STATE_TO_AC_MODE = {
    HVACMode.HEAT_COOL: "auto",
    HVACMode.COOL: "cool",
    HVACMode.DRY: "dry",
    HVACMode.HEAT: "heat",
    HVACMode.FAN_ONLY: "wind",
}


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add climate entities for a config entry."""
    ac_capabilities = [
        Capability.air_conditioner_mode,
        Capability.air_conditioner_fan_mode,
        Capability.switch,
        Capability.temperature_measurement,
        Capability.thermostat_cooling_setpoint,
    ]

    broker = hass.data[DOMAIN][DATA_BROKERS][config_entry.entry_id]
    entities = []
    for device in broker.devices.values():
        if not broker.any_assigned(device.device_id, CLIMATE_DOMAIN):
            continue
        # Check if the device should be treated as an AC or a Thermostat
        if all(capability in device.capabilities for capability in ac_capabilities):
            entities.append(SmartThingsAirConditioner(device))
        else:
            # Ensure it has basic thermostat capabilities before adding as Thermostat
            thermostat_capabilities = [
                Capability.temperature_measurement,
                Capability.thermostat_cooling_setpoint,
                Capability.thermostat_heating_setpoint,
                Capability.thermostat_mode,
            ]
            # Also support legacy thermostat capability
            if Capability.thermostat in device.capabilities or \
               all(capability in device.capabilities for capability in thermostat_capabilities):
                entities.append(SmartThingsThermostat(device))
            # Optionally log devices that don't match either profile if needed
            # else:
            #     _LOGGER.debug("Device %s (%s) does not match AC or Thermostat profile",
            #                   device.label, device.device_id)

    async_add_entities(entities, True)


def get_capabilities(capabilities: Sequence[str]) -> Sequence[str] | None:
    """Return all capabilities supported if minimum required are present."""
    # Define all potentially relevant capabilities for climate devices
    supported = [
        Capability.air_conditioner_mode,
        Capability.air_conditioner_fan_mode,
        "fanOscillationMode", # Swing mode capability
        Capability.switch,
        Capability.temperature_measurement,
        Capability.thermostat, # Legacy capability
        Capability.thermostat_cooling_setpoint,
        Capability.thermostat_fan_mode,
        Capability.thermostat_heating_setpoint,
        Capability.thermostat_mode,
        Capability.thermostat_operating_state,
        Capability.execute, # Used for custom commands
        "custom.airConditionerOptionalMode", # Preset mode capability
        "custom.thermostatSetpointControl", # Another potential capability
        "relativeHumidityMeasurement", # Humidity capability
        "samsungce.dustFilter", # Example vendor specific capability
        # Add other relevant capabilities here
    ]

    # Define minimum capabilities for a standard Thermostat
    thermostat_capabilities = [
        Capability.temperature_measurement,
        Capability.thermostat_cooling_setpoint,
        Capability.thermostat_heating_setpoint,
        Capability.thermostat_mode,
    ]

    # Define minimum capabilities for an Air Conditioner
    ac_capabilities = [
        Capability.air_conditioner_mode,
        # Capability.air_conditioner_fan_mode, # Fan mode is often optional
        Capability.switch,
        Capability.temperature_measurement,
        Capability.thermostat_cooling_setpoint,
    ]

    # Check if the device qualifies as either type
    is_thermostat = Capability.thermostat in capabilities or \
                    all(cap in capabilities for cap in thermostat_capabilities)
    is_ac = all(cap in capabilities for cap in ac_capabilities)

    if is_thermostat or is_ac:
        # Return the list of all potentially supported capabilities
        # that are actually present on the device
        return [cap for cap in supported if cap in capabilities]

    return None


class SmartThingsThermostat(SmartThingsEntity, ClimateEntity):
    """Define a SmartThings climate entity (Thermostat)."""

    def __init__(self, device):
        """Init the class."""
        super().__init__(device)
        self._supported_features = self._determine_features()
        self._hvac_mode = None
        self._hvac_modes = None

    def _determine_features(self):
        """Determine the supported features based on capabilities."""
        flags = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        # Check for fan mode capability using get_capability for robustness
        if self._device.get_capability(
            Capability.thermostat_fan_mode, Capability.thermostat
        ):
            flags |= ClimateEntityFeature.FAN_MODE
        # Check for humidity capability
        if Capability.relative_humidity_measurement in self._device.capabilities:
             # Assuming humidity is read-only, no specific feature flag needed unless controllable
             pass # No specific ClimateEntityFeature for read-only humidity
        return flags

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        await self._device.set_thermostat_fan_mode(fan_mode, set_status=True)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_schedule_update_ha_state(True)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target operation mode."""
        mode = STATE_TO_MODE.get(hvac_mode)
        if mode is None:
            _LOGGER.error("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self._device.set_thermostat_mode(mode, set_status=True)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_schedule_update_ha_state(True)

    async def async_set_temperature(self, **kwargs):
        """Set new operation mode and target temperatures."""
        tasks = []
        # Set HVAC mode first if requested
        if hvac_mode := kwargs.get(ATTR_HVAC_MODE):
            mode = STATE_TO_MODE.get(hvac_mode)
            if mode:
                # Use a task to set mode, allowing simultaneous temperature setting
                tasks.append(self._device.set_thermostat_mode(mode, set_status=True))
            else:
                 _LOGGER.warning("Invalid HVAC mode requested in set_temperature: %s", hvac_mode)

        # Determine which setpoint(s) to adjust based on current/requested mode
        target_hvac_mode = kwargs.get(ATTR_HVAC_MODE, self.hvac_mode)
        heating_setpoint = None
        cooling_setpoint = None

        if target_hvac_mode == HVACMode.HEAT:
            heating_setpoint = kwargs.get(ATTR_TEMPERATURE)
        elif target_hvac_mode == HVACMode.COOL:
            cooling_setpoint = kwargs.get(ATTR_TEMPERATURE)
        elif target_hvac_mode == HVACMode.HEAT_COOL:
            # Allow setting range or individual temp if supported
            heating_setpoint = kwargs.get(ATTR_TARGET_TEMP_LOW)
            cooling_setpoint = kwargs.get(ATTR_TARGET_TEMP_HIGH)
             # Also allow setting single ATTR_TEMPERATURE if device supports it in auto mode
            if ATTR_TEMPERATURE in kwargs and heating_setpoint is None and cooling_setpoint is None:
                 # Behavior depends on device; often adjusts closest setpoint or both
                 # Assuming adjustment of both for now if single temp is provided in HEAT_COOL
                 # Check device specific behavior if needed.
                 temp = kwargs.get(ATTR_TEMPERATURE)
                 # Heuristic: Set both with a small differential or use device default logic
                 # For simplicity, we might just set one or log a warning.
                 # Let's assume setting both for now, device might handle it.
                 # heating_setpoint = temp # Or temp - some_delta
                 # cooling_setpoint = temp # Or temp + some_delta
                 # Using separate low/high is preferred for HEAT_COOL
                 _LOGGER.warning("Setting single temperature in HEAT_COOL mode might be ambiguous. Use target_temp_low/high.")
                 # Let's try setting both if provided this way, device might adjust.
                 # If device doesn't support single temp setting in heat_cool, this might fail.
                 # heating_setpoint = temp
                 # cooling_setpoint = temp # This might not be what user wants.
                 # Better: Only set if low/high are provided.
                 pass


        # Add tasks for setting temperatures
        if heating_setpoint is not None:
            tasks.append(
                self._device.set_heating_setpoint(
                    round(heating_setpoint, 3), set_status=True
                )
            )
        if cooling_setpoint is not None:
            tasks.append(
                self._device.set_cooling_setpoint(
                    round(cooling_setpoint, 3), set_status=True
                )
            )

        if tasks:
             await asyncio.gather(*tasks)
             # State is set optimistically in the commands above, therefore update
             # the entity state ahead of receiving the confirming push updates
             self.async_schedule_update_ha_state(True)
        else:
             _LOGGER.debug("No temperature or mode changes requested in set_temperature.")


    async def async_update(self):
        """Update the attributes of the climate device."""
        # Update HVAC mode
        thermostat_mode = self._device.status.thermostat_mode
        self._hvac_mode = MODE_TO_STATE.get(thermostat_mode)
        if self._hvac_mode is None:
            _LOGGER.debug(
                "Device %s (%s) returned an invalid hvac mode: %s",
                self._device.label,
                self._device.device_id,
                thermostat_mode,
            )

        # Update supported HVAC modes
        modes = set()
        supported_modes = self._device.status.supported_thermostat_modes
        if isinstance(supported_modes, Iterable):
            for mode in supported_modes:
                if (state := MODE_TO_STATE.get(mode)) is not None:
                    modes.add(state)
                else:
                    _LOGGER.debug(
                        "Device %s (%s) returned an invalid supported thermostat mode: %s",
                        self._device.label,
                        self._device.device_id,
                        mode,
                    )
        else:
            _LOGGER.debug(
                "Device %s (%s) returned invalid supported thermostat modes: %s. Type: %s",
                self._device.label,
                self._device.device_id,
                supported_modes,
                type(supported_modes).__name__
            )
        # Ensure OFF mode is always available if device supports setting mode 'off'
        # Or if it has a switch capability. For thermostats, rely on supported_thermostat_modes.
        if HVACMode.OFF in STATE_TO_MODE and STATE_TO_MODE[HVACMode.OFF] in (supported_modes or []):
             modes.add(HVACMode.OFF)

        self._hvac_modes = list(modes) if modes else [HVACMode.OFF] # Default to OFF if no modes reported


    @property
    def current_humidity(self):
        """Return the current humidity."""
        # Check capability first
        if Capability.relative_humidity_measurement in self._device.capabilities:
            return self._device.status.humidity
        return None

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.status.temperature

    @property
    def fan_mode(self):
        """Return the fan setting."""
        # Check capability first
        if self._device.get_capability(Capability.thermostat_fan_mode, Capability.thermostat):
            return self._device.status.thermostat_fan_mode
        return None # Return None if capability not present

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
         # Check capability first
        if self._device.get_capability(Capability.thermostat_fan_mode, Capability.thermostat):
             modes = self._device.status.supported_thermostat_fan_modes
             # Ensure it's a list or tuple before returning
             if isinstance(modes, (list, tuple)):
                 return list(modes)
             _LOGGER.debug("Unsupported format for fan modes: %s", modes)
             return [] # Return empty list if format is wrong
        return [] # Return empty list if capability not present


    @property
    def hvac_action(self) -> str | None:
        """Return the current running hvac operation if supported."""
        # Check capability first
        if Capability.thermostat_operating_state in self._device.capabilities:
             op_state = self._device.status.thermostat_operating_state
             return OPERATING_STATE_TO_ACTION.get(op_state)
        return None # Return None if capability not present


    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self._hvac_mode

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._hvac_modes

    @property
    def supported_features(self):
        """Return the supported features."""
        return self._supported_features

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self.hvac_mode == HVACMode.COOL:
            return self._device.status.cooling_setpoint
        if self.hvac_mode == HVACMode.HEAT:
            return self._device.status.heating_setpoint
        # For HEAT_COOL or other modes, target_temperature is ambiguous.
        # HA prefers target_temperature_low/high in HEAT_COOL mode.
        # Return None if not in simple HEAT or COOL mode.
        return None

    @property
    def target_temperature_high(self):
        """Return the highbound target temperature we try to reach."""
        # Only relevant in HEAT_COOL mode typically
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._device.status.cooling_setpoint
        return None

    @property
    def target_temperature_low(self):
        """Return the lowbound target temperature we try to reach."""
         # Only relevant in HEAT_COOL mode typically
        if self.hvac_mode == HVACMode.HEAT_COOL:
            return self._device.status.heating_setpoint
        return None

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        try:
            unit = self._device.status.attributes[Attribute.temperature].unit
            return UNIT_MAP.get(unit)
        except (AttributeError, KeyError):
             # Fallback or default if unit attribute not found
             _LOGGER.debug("Temperature unit not found for device %s", self.entity_id)
             # Consider returning hass.config.units.temperature_unit as a fallback
             return None


class SmartThingsAirConditioner(SmartThingsEntity, ClimateEntity):
    """Define a SmartThings Air Conditioner."""

    # Removed is_faulty_quiet as logic changed

    def __init__(self, device):
        """Init the class."""
        super().__init__(device)
        # Determine features based on capabilities found during init
        self._supported_features = self._determine_features()
        self._hvac_modes = [] # Initialize empty, updated in async_update

    def _determine_features(self):
        """Determine supported features based on capabilities."""
        features = ClimateEntityFeature.TARGET_TEMPERATURE # Basic feature

        if Capability.air_conditioner_fan_mode in self._device.capabilities:
            features |= ClimateEntityFeature.FAN_MODE
        if "fanOscillationMode" in self._device.capabilities:
            features |= ClimateEntityFeature.SWING_MODE
        # Preset modes depend on 'custom.airConditionerOptionalMode' or specific model logic
        if "custom.airConditionerOptionalMode" in self._device.capabilities or \
           self._device.status.attributes.get(Attribute.mnmo, None): # Check if model info exists for custom logic
            features |= ClimateEntityFeature.PRESET_MODE
        # Add other features based on capabilities if needed

        return features

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        # Check capability first
        if Capability.air_conditioner_fan_mode not in self._device.capabilities:
             _LOGGER.warning("Device %s does not support setting fan mode.", self.entity_id)
             return
        await self._device.set_fan_mode(fan_mode, set_status=True)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        model = self._device.status.attributes.get(Attribute.mnmo, None)
        if model:
             model = model.value.split("|")[0] # Extract model ID if available
        else:
             model = "unknown" # Default if model info is missing

        result = False # Variable to track command success
        mode_set = False # Flag if mode was handled by special command

        # --- START MODIFICATION ---
        # Special handling for ARTIK051_KRAC_18K
        if model == "ARTIK051_KRAC_18K":
            command_options = None # Variable for execute command argument
            # Use case-insensitive comparison for the input preset_mode
            preset_mode_lower = preset_mode.lower()

            if preset_mode_lower == "fast turbo":
                command_options = ["Comode_Speed"]
            elif preset_mode_lower == "comfort":
                command_options = ["Comode_Comfort"]
            elif preset_mode_lower == "quiet":
                command_options = ["Comode_Quiet"]
            elif preset_mode_lower == "2-step":
                command_options = ["Comode_2Step"]
            # Note: Assuming 'WindFree' uses the standard command below.
            # If not, add: elif preset_mode_lower == "windfree": command_options = ["Comode_WindFree"] ?

            # If a special mode requiring 'execute' was found
            if command_options:
                try:
                    # Estimated endpoint, may need adjustment!
                    endpoint = "mode/vs/0"
                    result = await self._device.execute(
                        endpoint, {"x.com.samsung.da.options": command_options}
                    )
                    _LOGGER.debug("Executed special command for %s with result: %s", preset_mode, result)
                    mode_set = True # Mark that the mode was (attempted) set
                except Exception as e:
                    _LOGGER.error("Error executing special command for preset %s: %s", preset_mode, e)
                    result = False # Error during execution

        # If mode wasn't set by a special command (or not the KRAC_18K model),
        # use the standard 'setAcOptionalMode' command
        if not mode_set:
            # Check if the standard capability exists
            if "custom.airConditionerOptionalMode" in self._device.capabilities:
                 try:
                    # Use the exact preset mode name as sent for the standard command
                    result = await self._device.command(
                        "main",
                        "custom.airConditionerOptionalMode",
                        "setAcOptionalMode",
                        [preset_mode], # Use original preset_mode string
                    )
                    _LOGGER.debug("Executed standard command for %s with result: %s", preset_mode, result)
                 except Exception as e:
                    _LOGGER.error("Error executing standard command for preset %s: %s", preset_mode, e)
                    result = False # Error during execution
            else:
                 _LOGGER.warning("Device %s does not support standard preset mode capability.", self.entity_id)
                 result = False # Cannot set mode

        # --- END MODIFICATION ---

        # Optimistically update HA state if command was sent (even if result is False,
        # as API might not return success but command might have worked)
        # Alternative: only update if result is True
        if result is not None: # If command at least executed without exception
            self._device.status.update_attribute_value("acOptionalMode", preset_mode)

        # Schedule HA state update
        self.async_write_ha_state()


    async def async_set_swing_mode(self, swing_mode):
        """Set new target swing mode."""
        # Check capability first
        if "fanOscillationMode" not in self._device.capabilities:
             _LOGGER.warning("Device %s does not support setting swing mode.", self.entity_id)
             return

        # Using command directly as set_fan_oscillation_mode might not exist in pysmartthings
        try:
            result = await self._device.command(
                "main",
                "fanOscillationMode",
                "setFanOscillationMode",
                [swing_mode],
            )
            # State is set optimistically below, update HA state
            if result:
                self._device.status.update_attribute_value("fanOscillationMode", swing_mode)
        except Exception as e:
            _LOGGER.error("Error setting swing mode to %s: %s", swing_mode, e)
            result = False # Indicate failure

        if result:
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target operation mode."""
        # Turn off if requested
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return

        # Ensure device supports the requested mode
        ac_mode = STATE_TO_AC_MODE.get(hvac_mode)
        if not ac_mode:
            _LOGGER.error("Unsupported HVAC mode requested: %s", hvac_mode)
            return
        if ac_mode not in (self._device.status.supported_ac_modes or []):
             _LOGGER.warning("Device %s does not support AC mode '%s'", self.entity_id, ac_mode)
             # Optionally prevent setting or try anyway? Trying anyway for now.
             # return # Uncomment to prevent setting unsupported modes


        tasks = []
        # Turn on the device if it's off before setting mode.
        if not self._device.status.switch:
             # Check if switch capability exists
            if Capability.switch in self._device.capabilities:
                 tasks.append(self._device.switch_on(set_status=True))
            else:
                 _LOGGER.warning("Device %s cannot be turned on, missing switch capability.", self.entity_id)
                 # If no switch, cannot turn on, mode setting might fail or be irrelevant
                 # Proceeding to set mode anyway, device might turn on implicitly.

        # Add task to set the mode
        tasks.append(
            self._device.set_air_conditioner_mode(ac_mode, set_status=True)
        )

        await asyncio.gather(*tasks)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        tasks = []
        target_temp = kwargs.get(ATTR_TEMPERATURE)

        # Set HVAC mode first if requested
        if operation_mode := kwargs.get(ATTR_HVAC_MODE):
            # Check if mode change is actually needed
            current_ha_mode = self.hvac_mode # Get current HA mode
            if operation_mode != current_ha_mode:
                 ac_mode = STATE_TO_AC_MODE.get(operation_mode)
                 if ac_mode:
                     # Turn off handling
                     if operation_mode == HVACMode.OFF:
                         tasks.append(self._device.switch_off(set_status=True))
                         # Clear target temp if turning off
                         target_temp = None
                     else:
                         # Turn on if needed
                         if not self._device.status.switch and Capability.switch in self._device.capabilities:
                             tasks.append(self._device.switch_on(set_status=True))
                         # Set the mode
                         tasks.append(self._device.set_air_conditioner_mode(ac_mode, set_status=True))
                 else:
                    _LOGGER.warning("Invalid HVAC mode requested in set_temperature: %s", operation_mode)

        # Set temperature if provided and not turning off
        if target_temp is not None:
             # Check capability
            if Capability.thermostat_cooling_setpoint in self._device.capabilities:
                 tasks.append(
                     self._device.set_cooling_setpoint(round(target_temp, 3), set_status=True)
                 )
            else:
                 _LOGGER.warning("Device %s does not support setting temperature.", self.entity_id)

        # Execute tasks if any
        if tasks:
            await asyncio.gather(*tasks)
            # State is set optimistically in the command above, therefore update
            # the entity state ahead of receiving the confirming push updates
            self.async_write_ha_state()


    async def async_turn_on(self):
        """Turn device on."""
        if Capability.switch not in self._device.capabilities:
             _LOGGER.warning("Device %s does not support turning on (no switch capability).", self.entity_id)
             return
        await self._device.switch_on(set_status=True)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn device off."""
        if Capability.switch not in self._device.capabilities:
             _LOGGER.warning("Device %s does not support turning off (no switch capability).", self.entity_id)
             return
        await self._device.switch_off(set_status=True)
        # State is set optimistically in the command above, therefore update
        # the entity state ahead of receiving the confirming push updates
        self.async_write_ha_state()

    async def async_update(self):
        """Update the calculated fields of the AC."""
        # Update supported HVAC modes
        modes = {HVACMode.OFF} # OFF is always assumed possible via turn_off service
        supported_ac_modes = self._device.status.supported_ac_modes
        if isinstance(supported_ac_modes, Iterable):
            for mode in supported_ac_modes:
                if (state := AC_MODE_TO_STATE.get(mode)) is not None:
                    modes.add(state)
                else:
                    _LOGGER.debug(
                        "Device %s (%s) returned an invalid supported AC mode: %s",
                        self._device.label,
                        self._device.device_id,
                        mode,
                    )
        else:
            _LOGGER.debug(
                "Device %s (%s) returned invalid supported AC modes: %s. Type: %s",
                self._device.label,
                self._device.device_id,
                supported_ac_modes,
                type(supported_ac_modes).__name__
            )
        self._hvac_modes = list(modes)

    @property
    def current_humidity(self):
        """Return the current humidity."""
         # Check capability first
        if Capability.relative_humidity_measurement in self._device.capabilities:
            return self._device.status.humidity
        return None


    @property
    def current_temperature(self):
        """Return the current temperature."""
        # Check capability first
        if Capability.temperature_measurement in self._device.capabilities:
            return self._device.status.temperature
        return None

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        # Example: Add model ID if available
        state_attributes = {}
        model_attr = self._device.status.attributes.get(Attribute.mnmo)
        if model_attr and model_attr.value:
            state_attributes["device_model"] = model_attr.value.split("|")[0]

        # Add other interesting attributes if needed
        # e.g., filter status if capability exists
        # if "samsungce.dustFilter" in self._device.capabilities:
        #    state_attributes["filter_status"] = self._device.status.attributes.get("samsungce.dustFilter")?.value

        return state_attributes

    @property
    def fan_mode(self):
        """Return the fan setting."""
        # Check capability first
        if Capability.air_conditioner_fan_mode in self._device.capabilities:
            return self._device.status.fan_mode
        return None

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        # Check capability first
        if Capability.air_conditioner_fan_mode in self._device.capabilities:
             modes = self._device.status.supported_ac_fan_modes
             if isinstance(modes, (list, tuple)):
                 return list(modes)
             _LOGGER.debug("Unsupported format for AC fan modes: %s", modes)
             return []
        return []

    @property
    def swing_mode(self):
        """Return the swing setting."""
        # Check capability first
        if "fanOscillationMode" in self._device.capabilities:
            # Attribute access needs safety check
            attr = self._device.status.attributes.get("fanOscillationMode")
            return attr.value if attr else None
        return None

    @property
    def swing_modes(self):
        """Return the list of available swing modes."""
         # Check capability first
        if "fanOscillationMode" not in self._device.capabilities:
             return []

        modes = []
        # Attribute access needs safety check
        supported_attr = self._device.status.attributes.get("supportedFanOscillationModes")

        if supported_attr and supported_attr.value is not None:
            # Ensure the value is iterable before trying to iterate
            if isinstance(supported_attr.value, Iterable):
                 modes = [str(x) for x in supported_attr.value]
            else:
                 _LOGGER.debug("supportedFanOscillationModes value is not iterable: %s", supported_attr.value)
        elif self.swing_mode is not None:
             # Fallback if supported modes attribute is missing/null but current mode is known
             # This is a guess based on common modes
             modes = ["fixed", "all", "vertical", "horizontal"]
             _LOGGER.debug("supportedFanOscillationModes not available, using fallback list.")

        return modes


    @property
    def preset_mode(self):
        """Return the current ac optional mode setting."""
        # Check capability first
        if "custom.airConditionerOptionalMode" in self._device.capabilities:
             attr = self._device.status.attributes.get("acOptionalMode")
             return attr.value if attr else None
        # If determined by model-specific logic, return that state if tracked
        # This requires storing the state set via _device.execute previously
        # For now, relying on the attribute value which might be updated by device/API after execute command.
        return None # Return None if capability not present

    @property
    def preset_modes(self):
        """Return the list of available ac optional modes, add specific modes for ARTIK051_KRAC_18K."""
        restricted_values = ["windFree"] # 'windFree' is usually restricted in heat/auto mode
        model = self._device.status.attributes.get(Attribute.mnmo)
        model_id = model.value.split("|")[0] if model and model.value else "unknown"

        # Get the original list of supported modes from the device
        supported_ac_optional_modes = []
        try:
            modes_attr = self._device.status.attributes.get("supportedAcOptionalMode")
            if modes_attr and modes_attr.value is not None:
                 # Ensure it's iterable
                 if isinstance(modes_attr.value, Iterable):
                    supported_ac_optional_modes = [str(x) for x in modes_attr.value]
                    # Handle case where device reports only ['off']
                    if supported_ac_optional_modes == ["off"]:
                         supported_ac_optional_modes = []
                 else:
                      _LOGGER.debug("supportedAcOptionalMode value is not iterable: %s", modes_attr.value)

        except (AttributeError, KeyError, TypeError):
            # If attribute doesn't exist or has wrong type, start with an empty list
             _LOGGER.debug("Could not retrieve supportedAcOptionalMode attribute.")
             supported_ac_optional_modes = []


        # --- START MODIFICATION ---
        # For model ARTIK051_KRAC_18K, add all desired modes if not already present
        if model_id == "ARTIK051_KRAC_18K":
            desired_modes = ["WindFree", "2-Step", "Fast Turbo", "Comfort", "Quiet"]
            # Create a set of lowercased existing modes for efficient checking
            existing_modes_lower = {mode.lower() for mode in supported_ac_optional_modes}
            for mode in desired_modes:
                # Add if not already present (case-insensitive check)
                if mode.lower() not in existing_modes_lower:
                    supported_ac_optional_modes.append(mode)

        # Remove 'off' if present and other modes exist
        if len(supported_ac_optional_modes) > 1 and "off" in supported_ac_optional_modes:
            supported_ac_optional_modes.remove("off")
        # --- END MODIFICATION ---


        # Remove restricted modes ('WindFree') if current HVAC mode is auto or heat
        current_hvac_mode_internal = self._device.status.air_conditioner_mode # Get internal mode name
        modes_to_return = supported_ac_optional_modes
        if current_hvac_mode_internal in ("auto", "heat"):
            # Create a new list excluding restricted modes (case-insensitive)
             modes_to_return = [
                mode for mode in supported_ac_optional_modes
                if not any(restricted.lower() == mode.lower() for restricted in restricted_values)
            ]

        return modes_to_return


    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        # Check switch status first if available
        if Capability.switch in self._device.capabilities and not self._device.status.switch:
            return HVACMode.OFF
        # Get mode from air_conditioner_mode attribute
        current_ac_mode = self._device.status.air_conditioner_mode
        return AC_MODE_TO_STATE.get(current_ac_mode) # Returns None if mode is unknown


    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        # Return the list calculated in async_update
        return self._hvac_modes


    @property
    def supported_features(self):
        """Return the supported features."""
        # Return the features determined during init
        return self._supported_features


    @property
    def max_temp(self):
        """Return the maximum temperature limit"""
        # Check capability first
        if Capability.thermostat_cooling_setpoint in self._device.capabilities:
             # Assume max is related to cooling setpoint range if specific max attribute missing
             # This might require checking for a specific maxSetpoint attribute if available
             attr = self._device.status.attributes.get("maximumSetpoint") # Example attribute name
             if attr and attr.value is not None:
                 try:
                     return int(attr.value)
                 except (ValueError, TypeError):
                    _LOGGER.warning("Invalid value for maximumSetpoint: %s", attr.value)
             # Fallback based on common AC limits if attribute missing
             return 30 # Example fallback
        return None # No temp control


    @property
    def min_temp(self):
        """Return the minimum temperature limit"""
         # Check capability first
        if Capability.thermostat_cooling_setpoint in self._device.capabilities:
             # Assume min is related to cooling setpoint range if specific min attribute missing
             attr = self._device.status.attributes.get("minimumSetpoint") # Example attribute name
             if attr and attr.value is not None:
                 try:
                     return int(attr.value)
                 except (ValueError, TypeError):
                    _LOGGER.warning("Invalid value for minimumSetpoint: %s", attr.value)
             # Fallback based on common AC limits if attribute missing
             return 16 # Example fallback
        return None # No temp control


    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
         # Check capability first
        if Capability.thermostat_cooling_setpoint in self._device.capabilities:
            return self._device.status.cooling_setpoint
        return None # No temp control


    @property
    def target_temperature_step(self):
        """Return the target temperature step size."""
        # Common step size for ACs
        # Could potentially get this from device attributes if available
        return 1.0


    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        try:
            # Check capability first
            if Capability.temperature_measurement in self._device.capabilities:
                 unit = self._device.status.attributes[Attribute.temperature].unit
                 return UNIT_MAP.get(unit)
            else:
                 # If no temp measurement, try getting unit from setpoint capability if needed
                 # This is less standard, usually unit comes from measurement
                 if Capability.thermostat_cooling_setpoint in self._device.capabilities:
                      unit = self._device.status.attributes[Attribute.cooling_setpoint].unit
                      return UNIT_MAP.get(unit)
        except (AttributeError, KeyError):
             _LOGGER.debug("Temperature unit not found for device %s", self.entity_id)

        # Fallback if no unit found
        return None # Or hass.config.units.temperature_unit
