#!/usr/bin/env python3

import json


def save_ambient_temp(temp):
    json_temp = {""}
    with open(AMBIENT_CACHE_FILE, "w") as f:
        json.dump(temp, f)