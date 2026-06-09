import json
from pathlib import Path

glb_path = Path("c:/Users/ben/Documents/Projects/Tertius-Web/cache/tertius/intus/3x5shed/output.glb")
with open(glb_path, "rb") as f:
    data = f.read()

import struct
magic, version, length = struct.unpack("<4sII", data[:12])
chunk_len, chunk_type = struct.unpack("<II", data[12:20])
json_data = data[20:20+chunk_len].decode("utf-8")
gltf_json = json.loads(json_data)

names = [node.get("name") for node in gltf_json.get("nodes", [])]
print(names)
