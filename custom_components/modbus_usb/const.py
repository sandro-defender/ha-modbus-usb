"""Constants for the Modbus USB Controller integration."""

DOMAIN = "modbus_usb"

# Platform names
PLATFORM_SENSOR = "sensor"
PLATFORM_SWITCH = "switch"
PLATFORM_BINARY_SENSOR = "binary_sensor"
PLATFORM_NUMBER = "number"

# --- Hub / connection config keys ---
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"
CONF_BYTESIZE = "bytesize"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"
CONF_SLAVE_ID = "slave_id"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_SLAVE_ID = 1
DEFAULT_SCAN_INTERVAL = 10  # seconds

BAUDRATE_OPTIONS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
PARITY_OPTIONS = {"N": "None", "E": "Even", "O": "Odd"}
STOPBITS_OPTIONS = [1, 2]
BYTESIZE_OPTIONS = [7, 8]

# --- Per-entity config keys (stored in options["entities"]) ---
CONF_ENTITIES = "entities"
CONF_ENTITY_ID = "id"            # internal unique id (uuid-ish)
CONF_ENTITY_TYPE = "entity_type"  # "sensor" or "switch"
CONF_NAME = "name"
CONF_REGISTER_TYPE = "register_type"
CONF_ADDRESS = "address"
CONF_DATA_TYPE = "data_type"
CONF_SCALE = "scale"
CONF_UNIT_OF_MEASUREMENT = "unit_of_measurement"
CONF_DEVICE_CLASS = "device_class"
CONF_STATE_CLASS = "state_class"
CONF_ON_VALUE = "on_value"
CONF_OFF_VALUE = "off_value"
# Number entity extras
CONF_MIN_VALUE = "min_value"
CONF_MAX_VALUE = "max_value"
CONF_STEP = "step"
CONF_MODE = "mode"  # "slider" | "box"

ENTITY_TYPES = ["sensor", "switch", "binary_sensor", "number"]

# Modbus register types we read from / write to
REGISTER_TYPE_HOLDING = "holding"     # read/write 16-bit registers
REGISTER_TYPE_INPUT = "input"         # read-only 16-bit registers
REGISTER_TYPE_COIL = "coil"           # read/write 1-bit
REGISTER_TYPE_DISCRETE = "discrete"   # read-only 1-bit

REGISTER_TYPES_SENSOR = [REGISTER_TYPE_HOLDING, REGISTER_TYPE_INPUT]
REGISTER_TYPES_SWITCH = [REGISTER_TYPE_COIL, REGISTER_TYPE_HOLDING]

DATA_TYPE_UINT16 = "uint16"
DATA_TYPE_INT16 = "int16"
DATA_TYPE_UINT32 = "uint32"
DATA_TYPE_INT32 = "int32"
DATA_TYPE_FLOAT32 = "float32"

DATA_TYPES = [
    DATA_TYPE_UINT16,
    DATA_TYPE_INT16,
    DATA_TYPE_UINT32,
    DATA_TYPE_INT32,
    DATA_TYPE_FLOAT32,
]

# How many 16-bit registers each data type spans
DATA_TYPE_WORD_COUNT = {
    DATA_TYPE_UINT16: 1,
    DATA_TYPE_INT16: 1,
    DATA_TYPE_UINT32: 2,
    DATA_TYPE_INT32: 2,
    DATA_TYPE_FLOAT32: 2,
}

DEVICE_CLASS_OPTIONS = [
    "none",
    "temperature",
    "humidity",
    "power",
    "energy",
    "voltage",
    "current",
    "pressure",
    "frequency",
    "battery",
]

STATE_CLASS_OPTIONS = ["none", "measurement", "total", "total_increasing"]

# Diagnostics / coordinator health keys
DIAG_TOTAL_READS = "total_reads"
DIAG_FAILED_READS = "failed_reads"
DIAG_LAST_ERROR = "last_error"
DIAG_CONSECUTIVE_FAILURES = "consecutive_failures"
DIAG_LAST_SUCCESS = "last_success"

# Service names
SERVICE_READ_REGISTER = "read_register"
SERVICE_WRITE_REGISTER = "write_register"

# Event raised after a service read completes
EVENT_REGISTER_READ = f"{DOMAIN}_register_read"
