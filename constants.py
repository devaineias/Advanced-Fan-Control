config_path = '/etc/fan-control'

HWMON_PATH = "/sys/class/hwmon/"

STATE_FILE = config_path + "/fan_state.json"

AMBIENT_CACHE_FILE = config_path + "/ambient.json"

COOLDOWN_DURATION = 30   # seconds
RAMP_STEP = 12           # Max PWM change per update

TEMP_TARGETS = {
    "cpu": 80,
    "vrm": 50,
    "nvme": 80,
    "ram": 60,
    "gpu": 65
}