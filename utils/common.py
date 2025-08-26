import os
import json
import time

import constants

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

def load_config():
    if os.path.exists(constants.config_path + "/config.json"):
        with open(constants.config_path + "/config.json", "r") as f:
            config = json.load(f)
        return config

def load_state():
    try:
        with open(constants.STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(constants.STATE_FILE, "w") as f:
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

        target_temp = max(constants.TEMP_TARGETS.get(t, 70) for t in cooling_targets)
        target_temp = max(target_temp, 71)

        if temp < 40:
            base_pwm = min_pwm
        elif temp < 65:
            base_pwm = scale(temp, 50, 70, min_pwm, 200)
        else:
            base_pwm = scale(temp, 70, target_temp, 200, 255)

        pwm = base_pwm

        if cooling_gpu and is_bottom:
            pwm += int(((gpu_fan * 0.3 + gpu_load * 0.2 + scale(gpu_temp, 40, 80, 0, 60)) * 0.5) / (num_fans * 2)) # change values depending on GPU

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

        last = fan_state.get(pwm_id) or {}
        last_pwm = last.get("pwm", min_pwm)
        last_active = last.get("last_active", 0)

        temp = sensor_readings.get("cpu", 0)
        target_temp = 70

        if temp < target_temp - 5:
            if timestamp - last_active < constants.COOLDOWN_DURATION:
                if pwm < last_pwm:
                    pwm = max(last_pwm - max(1, constants.RAMP_STEP // 2), pwm)
                else:
                    pwm = max(pwm, last_pwm)
            else:
                if pwm < last_pwm:
                    pwm = max(last_pwm - constants.RAMP_STEP, pwm)
                else:
                    pwm = min(last_pwm + constants.RAMP_STEP, pwm)
        else:
            if abs(pwm - last_pwm) > constants.RAMP_STEP:
                if pwm > last_pwm:
                    pwm = last_pwm + constants.RAMP_STEP
                else:
                    pwm = last_pwm - constants.RAMP_STEP

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

def create_event():
    return
