import json
import struct
from pathlib import Path

def patch_glb_names(glb_path, mapping):
    if not mapping: 
        return
    with open(glb_path, "rb") as f:
        data = f.read()
    
    magic, version, length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF": 
        return
    
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    if chunk_type != b"JSON": 
        print(f"FAILED chunk_type: {chunk_type}")
        return
    
    json_data = data[20:20+chunk_len].decode("utf-8")
    gltf_json = json.loads(json_data)
    
    changed = False
    for node in gltf_json.get("nodes", []):
        if node.get("name") in mapping:
            print("Patching", node["name"], "to", mapping[node["name"]])
            node["name"] = mapping[node["name"]]
            changed = True
    
    if not changed:
        print("NO CHANGES")
        return
    
    new_json_data = json.dumps(gltf_json, separators=(',', ':')).encode("utf-8")
    padding = (4 - len(new_json_data) % 4) % 4
    new_json_data += b' ' * padding
    
    new_chunk_len = len(new_json_data)
    new_length = length - chunk_len + new_chunk_len
    
    new_data = bytearray()
    new_data.extend(struct.pack("<4sII", magic, version, new_length))
    new_data.extend(struct.pack("<I4s", new_chunk_len, chunk_type))
    new_data.extend(new_json_data)
    new_data.extend(data[20+chunk_len:])
    
    with open(glb_path, "wb") as f:
        f.write(new_data)
    print("SAVED GLB")

mapping = {"=>[0:1:1:2]": "Portal 1"}
patch_glb_names("scratch/test_order.glb", mapping)

# Verify
with open("scratch/test_order.glb", "rb") as f:
    data = f.read()
chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
json_data = data[20:20+chunk_len].decode("utf-8")
gltf_json = json.loads(json_data)
names = [n["name"] for n in gltf_json["nodes"] if "name" in n]
print("After patch, is Portal 1 there?", "Portal 1" in names)
