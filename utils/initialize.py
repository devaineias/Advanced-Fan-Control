import os
import constants

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
    if not os.path.exists(constants.config_path): 
        os.mkdir(constants.config_path)
        print("Configuration folder %s created!" % constants.config_path)
    else:
        print("Configuration folder %s found" % constants.config_path)

    if os.path.exists(constants.config_path + "/config.json"):
        print("Config file found!")
        return True
    else:
        print("Finding fan PWM control paths...")
        subfolders = [ f.path for f in os.scandir(constants.HWMON_PATH) if f.is_dir() ]
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

        with open(constants.config_path + "/config.json", "w") as f:
            json.dump(config, f, indent=4)
        print("✅ Config saved.")

        return True