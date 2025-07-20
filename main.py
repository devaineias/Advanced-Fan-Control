#!/usr/bin/env python3

import time
import os
import subprocess
import json
import psutil
import re

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



# === CONFIGURATION ===

config_path = '/etc/fan-control'

HWMON_PATH = "/sys/class/hwmon/"

STATE_FILE = config_path + "/fan_state.json"

AMBIENT_CACHE_FILE = config_path + "/ambient.json"

#COOLDOWN_DURATION = 30   # seconds
RAMP_STEP = 12           # Max PWM change per update

TEMP_TARGETS = {
    "cpu": 80,
    "vrm": 50,
    "nvme": 80,
    "ram": 60,
    "gpu": 65
}

# === CONFIGURATION END ===



# Helper function to identify common label types
def match_sensor_label(label):
    label = label.lower()
    if "cpu socket" in label:
        return "CPU_SOCKET"
    if "package id" in label or "cpu die" in label or "coretemp" in label:
        return "CPU"
    elif "vrm" in label:
        return "VRM"
    elif "pch" in label or "chipset" in label:
        return "PCH"
    elif "nvme" in label or "composite" in label:
        return "NVMe"
    elif "ram" in label or "spd5118" in label:
        return "RAM"
    elif "system" in label and "fan" not in label:
        return "System"
    elif "wifi" in label or "iwlwifi" in label:
        return "WiFi"
    return None 

def find_min_stable_pwm(pwm_path, rpm_path):
    print("🧪 Starting minimum PWM detection... Press Ctrl+C to abort.")
    try:
        for pwm in range(0, 256, 5):
            set_pwm(pwm_path, pwm)
            time.sleep(5)
            rpm = read_int(rpm_path)
            print(f"PWM {pwm} → RPM: {rpm} RPM")

            if rpm > 200:
                print(f"✅ Fan(s) spun up at PWM {pwm}")
                while True:
                    confirm = input("Are all fans connected to this header running reliably at this speed? (y/n): ").strip().lower()
                    if confirm == 'y':
                        return pwm
                    elif confirm == 'n':
                        break  # Try next PWM value
                    else:
                        print("Please type 'y' or 'n'.")
    except KeyboardInterrupt:
        print("\n⚠️ Detection interrupted.")

    # Fallback manual input
    while True:
        fallback = input("Could not detect automatically. Enter a minimum stable PWM manually (0-255), or leave empty to skip: ").strip()
        if fallback == '':
            return 0
        try:
            fallback_val = int(fallback)
            if 0 <= fallback_val <= 255:
                return fallback_val
            else:
                print("Value must be between 0 and 255.")
        except ValueError:
            print("Invalid input, please enter a number.")


def initialize():
    if not os.path.exists(config_path): 
        os.mkdir(config_path)
        print("Configuration folder %s created!" % config_path)
    else:
        print("Configuration folder %s found" % config_path)

    if os.path.exists(config_path + "/config.json"):
        print("Config file found!")
        return True
    else:
        print("Finding fan PWM control paths...")
        subfolders = [ f.path for f in os.scandir(HWMON_PATH) if f.is_dir() ]
        n = len(subfolders)
        print(f'Found {n} monitors present...')

        temp_sensors_array = []

        for subfolder in subfolders:
            files = os.listdir(subfolder)
            for file in files:
                if "fan" in file:
                    fan_folder = subfolder
                if "temp" in file:
                    if subfolder not in temp_sensors_array:
                        temp_sensors_array.append(subfolder) 

        
        files = os.listdir(fan_folder)
        
        fan_pwm = []
        fan_speed = []
        
        for fan in files:
            if "pwm" in fan:
                if fan not in fan_pwm:
                    if "enable" not in fan:
                        fan_pwm.append(fan)
            if "_input" in fan:
                if "temp" not in fan:
                    if fan not in fan_speed:
                        if not fan.startswith("in"):
                            fan_speed.append(fan)
        
        print(f"Found folder containing fan control files, {fan_folder}")
        print(f"Temperature sensors folder(s): {temp_sensors_array}")
        
        print(f"Fan PWM files: {fan_pwm}")
        print(f"Fan Speed files: {fan_speed}")

        print("Checking PWM control...")

        # Test pwm control correlation

        config = {
            "fans": [],
            "sensors": []
        }

        for pwm in fan_pwm:
            print(f"Testing {fan_folder + "/" + pwm} ...")
            fan_input = fan_folder + "/" + "fan" + pwm.replace("pwm", "") + "_input"
            initial_speed = read_int(fan_input)
            print(f"Initial RPM: {initial_speed}")
            if initial_speed < 600:
                set_pwm(fan_folder + "/" + pwm, 255)
            else:
                set_pwm(fan_folder + "/" + pwm, 0)

            time.sleep(5)

            final_speed = read_int(fan_input)


            if abs(initial_speed - final_speed) > 300:  # Threshold for significant change
                print(f"✅ {pwm} controls {fan_input}")
                print("Fan or AIO?")
                pwm_type = input().strip().lower()

                fan_entry = {"pwm_file": fan_folder + "/" + pwm}

                if pwm_type == "aio":
                    print("Setting AIO Pump to 100%...")
                    set_pwm(fan_folder + "/" + pwm, 255)
                    fan_entry.update({
                        "type": "aio",
                        "fixed_speed": 255,
                        "notes": "Do not modify this PWM value. Always set to 100%."
                    })
                else:
                    fan_entry["type"] = "fan"
                    fan_entry["role"] = input(f"What is the role of {pwm}? (intake/exhaust): ").strip().lower()
                    fan_entry["location"] = input(f"Where is {pwm} installed? (e.g., front, top, side): ").strip().lower()
                    fan_entry["mounted_on"] = input(f"Is it mounted on something? (radiator/filter/mesh/panel/none): ").strip().lower()
                    fan_entry["cools_what"] = input(f"What is it responsible for cooling? (CPU, GPU, supply air, etc.): ").strip().lower()
                    fan_entry["number_of_fans"] = float(input(f"How many fans are attached to this header? "))
                    fan_entry["noise_level"] = float(input(f"On a scale of 1-10, how noisy is this fan? "))
                    fan_entry["cfm"] = float(input(f"Enter rated CFM for {pwm}: "))
                    fan_entry["max_pressure"] = float(input(f"Enter rated pressure (mmH₂O) for {pwm}: "))

                    fan_num = pwm.replace("pwm", "")
                    rpm_path = fan_folder + "/" + f"fan{fan_num}_input"
                    pwm_path = fan_folder + "/" + pwm
                    min_pwm = find_min_stable_pwm(pwm_path, rpm_path)

                    fan_entry["min_operating_pwm"] = min_pwm

                config["fans"].append(fan_entry) 
            else:
                print(f"❌ {pwm} does not control {fan_input}") 

        for sensor_folder in temp_sensors_array:
            try:
                with open(os.path.join(sensor_folder, "name"), "r") as name_file:
                    device_name = name_file.read().strip()
            except:
                continue

            # Only include sensors from the 'coretemp' device for CPU core temps
            is_coretemp = (device_name.lower() == "coretemp")

            files = os.listdir(sensor_folder)
            label_files = [f for f in files if f.startswith("temp") and f.endswith("_label")]

            for label_file in label_files:
                try:
                    with open(os.path.join(sensor_folder, label_file), "r") as f:
                        label_text = f.read().strip()
                except:
                    continue

                logical_label = match_sensor_label(label_text)

                # Optionally: skip "CPU" label if not from coretemp
                if logical_label == "cpu" and not is_coretemp:
                    continue

                if logical_label:
                    input_file = label_file.replace("_label", "_input")
                    sensor_path = os.path.join(sensor_folder, input_file)
                    if os.path.exists(sensor_path):
                        config["sensors"].append({
                            "sensor_path": sensor_path,
                            "label": logical_label
                        })



        print("Now writing config file...")

        with open(config_path + "/config.json", "w") as f:
            json.dump(config, f, indent=4)
        print("✅ Config saved.")

        return True

def load_config():
    if os.path.exists(config_path + "/config.json"):
        with open(config_path + "/config.json", "r") as f:
            config = json.load(f)
        return config

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def save_ambient_temp(temp):
    with open(AMBIENT_CACHE_FILE, "w") as f:
        json.dump(temp, f)

def read_gpu(type):
    try:
        res = subprocess.run(["nvidia-smi", f'--query-gpu={type}', "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True).stdout.strip()
        return res
    except:
        return None

def read_cpu(type):
    try:
        res = subprocess.run(["cpu-power"], check=True, capture_output=True, text=True).stdout
        return res
    except:
        return None

def read_temp_sensor(path):
    try:
        with open(path, "r") as f:
            return int(f.read().strip()) / 1000  # Convert millidegree to degree
    except:
        return None

def read_int(path):
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except:
        return -1

def set_pwm(path, value):
    try:
        enable_path = path + "_enable"
        if os.path.exists(enable_path):
            with open(enable_path, "r") as f:
                mode = f.read().strip()
            if mode != "1":
                with open(enable_path, "w") as f:
                    f.write("1")

        with open(path, "w") as f:
            f.write(str(value))
    except Exception as e:
        print(f"[PWM ERROR] {path}: {e}")

def scale(value, in_min, in_max, out_min, out_max):
    if in_max == in_min:
        return out_max if value >= in_max else out_min
    if value < in_min:
        return out_min
    if value > in_max:
        return out_max
    return ((value - in_min) * (out_max - out_min)) / (in_max - in_min) + out_min

def control_fans(config, sensor_readings, gpu_info, fans_speed):
    timestamp = time.time()
    fan_state = load_state()
    sensor_readings = {k.lower(): v for k, v in sensor_readings.items()}

    gpu_temp = gpu_info.get("temperature", 0)
    gpu_fan = gpu_info.get("fan_speed_percent", 0)
    gpu_load = gpu_info.get("load_percent", 0)

    intake_cfm_total = 0
    exhaust_cfm_total = 0
    fan_pwm_targets = {}

    # Calculate average temp for cooldown logic
    all_temps = [v for k, v in sensor_readings.items() if k in ["cpu", "vrm", "nvme", "system"]]
    avg_temp = sum(all_temps) / len(all_temps) if all_temps else 35

    # Update global cooldown state
    cooldown = fan_state.get("__cooldown__", {"start_time": None, "peak_temp": 0})
    if avg_temp > 40:
        if not cooldown["start_time"]:
            cooldown["start_time"] = timestamp
        cooldown["peak_temp"] = max(cooldown.get("peak_temp", 0), avg_temp)
    else:
        cooldown = {"start_time": None, "peak_temp": 0}
    cooldown_active = cooldown["start_time"] is not None
    sustained_time = timestamp - cooldown["start_time"] if cooldown_active else 0

    # First pass: calculate base PWM and estimated airflow per fan
    for fan in config["fans"]:
        pwm_id = fan["pwm_file"]

        if fan.get("type") == "aio":
            set_pwm(pwm_id, fan["fixed_speed"])
            continue

        cooling_targets = [t.strip().lower() for t in fan["cools_what"].split(",")]
        min_pwm = fan.get("min_operating_pwm", 0)
        noise_factor = fan.get("noise_level", 0) / 10
        is_intake = fan.get("role", "").lower() == "intake"
        is_bottom = fan.get("location", "").lower() == "bottom"
        is_air_supply = "air" in cooling_targets
        cooling_gpu = "gpu" in cooling_targets
        num_fans = fan.get("number_of_fans", 1)

        relevant_temps = []
        for target in cooling_targets:
            if target in sensor_readings:
                relevant_temps.append(sensor_readings[target])
            elif target == "gpu":
                relevant_temps.append(gpu_temp)

        temp = max(relevant_temps) if relevant_temps else None
        if temp is None:
            if is_intake:
                fallback_targets = ["cpu", "vrm", "nvme", "ram"]
                fallback_temps = [sensor_readings[t] for t in fallback_targets if t in sensor_readings]
                temp = max(fallback_temps) if fallback_temps else 35
            else:
                continue

        target_temp = max(TEMP_TARGETS.get(t, 70) for t in cooling_targets)
        target_temp = max(target_temp, 71)

        if temp < 40:
            base_pwm = min_pwm
        elif temp < 65:
            base_pwm = scale(temp, 50, 70, min_pwm, 200)
        else:
            base_pwm = scale(temp, 70, target_temp, 200, 255)

        pwm = base_pwm

        if cooling_gpu and is_bottom:
            pwm += int(((gpu_fan * 0.3 + gpu_load * 0.2 + scale(gpu_temp, 40, 80, 0, 60)) * 0.5) / (num_fans * 2))

        pwm += 10 if is_intake else -5

        if is_air_supply:
            pwm += 20

        penalty_factor = 1.0 if temp >= 70 else (1.1 - noise_factor)
        pwm *= penalty_factor

        pwm = max(min_pwm, min(int(pwm), 255))
        fan_pwm_targets[pwm_id] = pwm

        # Estimate real airflow using RPM ratio
        current_rpm = fans_speed.get(pwm_id, 0)
        max_rpm = fan_state.get(pwm_id, {}).get("max_rpm", 0)
        if current_rpm > max_rpm:
            max_rpm = current_rpm

        rpm_ratio = current_rpm / max_rpm if max_rpm else pwm / 255

        rated_cfm = fan.get("cfm", 0)
        resistance = 0.3
        if "filter" in fan.get("mounted_on", ""): resistance += 0.4
        if "radiator" in fan.get("mounted_on", ""): resistance += 0.5
        if "panel" in fan.get("mounted_on", ""): resistance += 0.4

        max_pressure = fan.get("max_pressure", 2.0)
        k = 2
        pressure_factor = max(0, min((1 - resistance / max_pressure) ** k if max_pressure else 1.0, 1.0))

        estimated_cfm = rated_cfm * rpm_ratio * pressure_factor * num_fans

        if is_intake:
            intake_cfm_total += estimated_cfm
        else:
            exhaust_cfm_total += estimated_cfm

        fan_state.setdefault(pwm_id, {})["max_rpm"] = max_rpm

    pressure_ratio = (intake_cfm_total + 1) / (exhaust_cfm_total + 1)
    intake_boost = 1.0 + (1.05 - pressure_ratio) if pressure_ratio < 1.05 else 1.0

    for fan in config["fans"]:
        pwm_id = fan["pwm_file"]
        if pwm_id not in fan_pwm_targets:
            continue

        pwm = fan_pwm_targets[pwm_id]
        is_intake = fan.get("role", "").lower() == "intake"
        min_pwm = fan.get("min_operating_pwm", 0)

        if is_intake:
            pwm = min(255, int(pwm * intake_boost))

        last = fan_state.get(pwm_id, {"pwm": min_pwm, "last_active": 0})
        last_pwm = last["pwm"]
        last_active = last.get("last_active", 0)

        temp = sensor_readings.get("cpu", 0)
        target_temp = 70

        if temp < target_temp - 5:
            if timestamp - last_active < COOLDOWN_DURATION:
                if pwm < last_pwm:
                    pwm = max(last_pwm - max(1, RAMP_STEP // 2), pwm)
                else:
                    pwm = max(pwm, last_pwm)
            else:
                if pwm < last_pwm:
                    pwm = max(last_pwm - RAMP_STEP, pwm)
                else:
                    pwm = min(last_pwm + RAMP_STEP, pwm)
        else:
            if abs(pwm - last_pwm) > RAMP_STEP:
                if pwm > last_pwm:
                    pwm = last_pwm + RAMP_STEP
                else:
                    pwm = last_pwm - RAMP_STEP

        if cooldown_active:
            peak_temp = cooldown.get("peak_temp", 40)
            boost = min(0.2, ((peak_temp - 40) / 60) * (sustained_time / 120))
            pwm = min(255, int(pwm * (1 + boost)))

        pwm = max(min_pwm, min(int(pwm), 255))
        print(f"[{pwm_id}] final pwm: {pwm}")
        set_pwm(pwm_id, pwm)

        fan_state[pwm_id] = {
            "pwm": pwm,
            "last_active": timestamp if temp >= target_temp else last_active,
            "max_rpm": fan_state[pwm_id]["max_rpm"]
        }

    fan_state["__cooldown__"] = cooldown
    save_state(fan_state)



def daemonize():
    if os.fork() > 0:
        exit()
    os.setsid()
    if os.fork() > 0:
        exit()

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
    #daemonize()
    print("Starting fan control debug session")
    print("Initializing...")
    if initialize() != True:
        print("Something went wrong")
    main()