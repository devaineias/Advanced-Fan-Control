#!/usr/bin/env python3

import time
import subprocess
import json
import psutil
import re

from utils.initialize import initialize
from utils.common import *

# === NOTES ===

# Unit is °C

# PWM 1 is CPU fan
# PWM 2 is AIO Pump, should always be at 100%, do not change.
# PWM 3 should be dependent on GPU fans
# PWM 4 is bottom right fan
# PWM 5 is middle side fan
# PWM 6 is 2 side fans
# PWM 7 is 2 CPU fans


# To calculate fan airflow, accounting for different factors, use this formula:
# Adjusted CFM = Rated CFM × (1 - Resistance / Max Static Pressure)^k

# For resistance:
# Dust filter (nylon/magnetic): 	0.3 – 0.8 mmH₂O
# Radiator (27mm):                  1.5 – 3.0 mmH₂O
# Mesh panel:                       0.2 – 0.6 mmH₂O
# Panel Grill:                      0.5 – 1.2 mmH₂O
#     return


# To calculate fan airflow, accounting for different factors, use this formula:
# Adjusted CFM = Rated CFM × (1 - Resistance / Max Static Pressure)^k

# For resistance:
# Dust filter (nylon/magnetic): 	0.3 – 0.8 mmH₂O
# Radiator (27mm):                  1.5 – 3.0 mmH₂O
# Mesh panel:                       0.2 – 0.6 mmH₂O
# Panel Grill:                      0.5 – 1.2 mmH₂O


def main():
    config = load_config()

    while True:
        # --- Read sensor temperatures ---
        sensor_readings = {}
        for sensor in config["sensors"]:
            try:
                temp = read_temp_sensor(sensor["sensor_path"])
                sensor_readings[sensor["label"].lower()] = temp
            except Exception as e:
                print(f"Failed to read {sensor['label']}: {e}")


        # --- Read GPU data ---
        gpu_info = {
            "temperature": float(read_gpu("temperature.gpu") or 0),
            "fan_speed_percent": float(read_gpu("fan.speed") or 0),
            "load_percent": float(read_gpu("utilization.gpu") or 0),
        }

        fan_readings = {}
        for fan in config["fans"]:
            pwm_path = fan["pwm_file"]

            # Extract the directory and PWM number
            dir_path = os.path.dirname(pwm_path)  # /sys/class/hwmon/hwmon6
            pwm_filename = os.path.basename(pwm_path)  # pwm5

            if not pwm_filename.startswith("pwm"):
                continue

            pwm_number = pwm_filename.replace("pwm", "")  # "5"
            fan_input_file = os.path.join(dir_path, f"fan{pwm_number}_input")

            try:
                with open(fan_input_file, "r") as f:
                    rpm = int(f.read().strip())
                    fan_readings[pwm_path] = rpm
            except FileNotFoundError:
                print(f"Fan input not found for {pwm_path}")
            except Exception as e:
                print(f"Error reading {fan_input_file}: {e}")


        # --- Apply fan control ---
        control_fans(config, sensor_readings, gpu_info, fan_readings)

        # --- Wait before next update ---
        time.sleep(1)

if __name__ == "__main__":
    print("Starting fan control debug session")
    print("Initializing...")
    if initialize() != True:
        print("Something went wrong")
    main()