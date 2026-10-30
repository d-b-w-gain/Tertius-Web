"""Export a Tertius model to glTF/GLB and patch Tertius metadata into it.

Moved out of the compile sandbox script so the monolithic model export and
per-component scene assets (#314) use the same code. The sandbox copies this
file into the project as ``tertius_glb_export.py``.
"""


def export_patched_gltf(
    compound,
    output_path,
    export_format,
    *,
    bd,
    deflection,
    angular_deflection,
    visual_tree,
    model_geometry,
    visual_source_map,
    part_component_names,
    end_phase=lambda name: None,
):
    bd.export_gltf(
        compound,
        str(output_path),
        binary=(export_format == "glb"),
        linear_deflection=deflection,
        angular_deflection=angular_deflection
    )
    end_phase("export")

    if export_format in ("gltf", "glb"):
        try:
            from build123d.exporters3d import _create_xde
            from OCP.XCAFDoc import XCAFDoc_DocumentTool
            from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
            from OCP.TDF import TDF_Tool
            from OCP.TDataStd import TDataStd_Name
            import json
            import struct

            # 1. Map tags to labels
            original_location = compound.location
            compound.location *= bd.Location((0, 0, 0), (1, 0, 0), -90)
            doc = _create_xde(compound, getattr(bd, "Unit").MM)
            shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
            tag_to_name = {}
            tag_to_color = {}

            def srgb_to_linear_float32(component):
                if component <= 0.04045:
                    linear = component / 12.92
                else:
                    linear = ((component + 0.055) / 1.055) ** 2.4
                return struct.unpack("<f", struct.pack("<f", linear))[0]

            def color_factor(color):
                rgba = None
                try:
                    if isinstance(color, bd.Color):
                        rgba = list(color)
                        rgba[:3] = [srgb_to_linear_float32(float(component)) for component in rgba[:3]]
                    elif hasattr(color, "to_tuple"):
                        rgba = list(color.to_tuple())
                    elif all(hasattr(color, attr) for attr in ("r", "g", "b")):
                        rgba = [color.r, color.g, color.b, getattr(color, "a", 1.0)]
                    elif all(hasattr(color, attr) for attr in ("red", "green", "blue")):
                        rgba = [color.red, color.green, color.blue, getattr(color, "alpha", 1.0)]
                except Exception:
                    rgba = None
                if not isinstance(rgba, list):
                    return None
                if len(rgba) == 3:
                    rgba.append(1.0)
                try:
                    return [float(component) for component in rgba[:4]]
                except Exception:
                    return None

            for node in bd.PreOrderIter(compound):
                if node.label or node.color is not None:
                    inst_label = shape_tool.FindShape(node.wrapped, findInstance=True)
                    if inst_label.IsNull():
                        inst_label = shape_tool.FindShape(node.wrapped, findInstance=False)

                    if not inst_label.IsNull():
                        entry = TCollection_AsciiString()
                        TDF_Tool.Entry_s(inst_label, entry)
                        tag = f"=>[{entry.ToCString()}]"
                        if node.label:
                            tag_to_name[tag] = node.label
                        if node.color is not None:
                            factor = color_factor(node.color)
                            if factor is not None:
                                tag_to_color[tag] = factor
                        TDataStd_Name.Set_s(inst_label, TCollection_ExtendedString(tag))

            compound.location = original_location

            # 2. Patch the exported scene metadata. Depending on the
            # exporter/backend, nodes may carry either XDE tags or labels.
            def is_generated_export_name(name):
                if not isinstance(name, str) or not name.strip():
                    return True
                stripped = name.strip()
                lowered = stripped.lower()
                return (
                    stripped.startswith("=>[")
                    or lowered in {"mesh", "node", "solid", "compound", "part", "object3d"}
                    or lowered.startswith(("node_", "mesh_"))
                )

            def patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_metadata):
                changed = False
                extras = gltf_json.setdefault("extras", {})
                if model_geometry is not None:
                    extras["tertiusModelGeometry"] = model_geometry
                    changed = True
                if visual_source_map.get("source_calls"):
                    extras["tertiusSourceMap"] = visual_source_map
                    changed = True

                def mark_authored_material(material):
                    material_changed = False
                    extras = material.setdefault("extras", {})
                    if extras.get("tertiusAuthoredColor") is not True:
                        extras["tertiusAuthoredColor"] = True
                        material_changed = True

                    base_color = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
                    if (
                        isinstance(base_color, list)
                        and len(base_color) >= 4
                        and isinstance(base_color[3], (int, float))
                        and float(base_color[3]) < 1.0
                        and material.get("alphaMode") != "BLEND"
                    ):
                        material["alphaMode"] = "BLEND"
                        material_changed = True

                    return material_changed

                for material in gltf_json.get("materials", []):
                    pbr = material.get("pbrMetallicRoughness")
                    if isinstance(pbr, dict) and "baseColorFactor" in pbr:
                        changed = mark_authored_material(material) or changed

                for node in gltf_json.get("nodes", []):
                    node_tag = node.get("name")
                    if node_tag in color_mapping and "mesh" in node:
                        mesh_index = node["mesh"]
                        meshes = gltf_json.get("meshes", [])
                        if isinstance(mesh_index, int) and 0 <= mesh_index < len(meshes):
                            for primitive in meshes[mesh_index].get("primitives", []):
                                material_index = primitive.get("material")
                                materials = gltf_json.get("materials", [])
                                if isinstance(material_index, int) and 0 <= material_index < len(materials):
                                    material = materials[material_index]
                                    pbr = material.setdefault("pbrMetallicRoughness", {})
                                    pbr["baseColorFactor"] = color_mapping[node_tag]
                                    changed = mark_authored_material(material) or changed
                    if node_tag in name_mapping:
                        node["name"] = name_mapping[node_tag]
                        changed = True

                nodes = gltf_json.get("nodes", [])

                def apply_visual_metadata(node_index, visual_node):
                    nonlocal changed
                    if not isinstance(node_index, int) or not isinstance(visual_node, dict):
                        return
                    if not (0 <= node_index < len(nodes)):
                        return
                    node = nodes[node_index]
                    if not isinstance(node, dict):
                        return

                    label = str(visual_node.get("label") or "")
                    source_call_ids = [
                        str(item)
                        for item in (visual_node.get("source_call_ids") or [])
                        if item
                    ]
                    component_id = visual_node.get("component_id")
                    product_key = visual_node.get("product_key")
                    product_digest = visual_node.get("product_definition_digest")
                    connection_id = visual_node.get("connection_id")
                    connection_digest = visual_node.get("connection_definition_digest")
                    part_number = visual_node.get("part_number")
                    if label and is_generated_export_name(node.get("name")):
                        node["name"] = label
                        changed = True
                    if source_call_ids:
                        extras = node.setdefault("extras", {})
                        extras["tertiusSourceCallIds"] = source_call_ids
                        changed = True
                    if component_id:
                        extras = node.setdefault("extras", {})
                        extras["tertiusComponent"] = {
                            "id": str(component_id),
                            "productKey": str(product_key or ""),
                            "productDefinitionDigest": str(product_digest or ""),
                        }
                        changed = True
                    if connection_id:
                        extras = node.setdefault("extras", {})
                        extras["tertiusConnection"] = {
                            "id": str(connection_id),
                            "connectionDefinitionDigest": str(connection_digest or ""),
                        }
                        changed = True
                    if part_number:
                        extras = node.setdefault("extras", {})
                        extras["tertiusPart"] = {
                            "partNumber": str(part_number),
                            "component": str(part_component_names.get(part_number, "")),
                        }
                        changed = True

                    gltf_children = [
                        child_index
                        for child_index in (node.get("children") or [])
                        if isinstance(child_index, int)
                    ]
                    visual_children = [
                        child
                        for child in (visual_node.get("children") or [])
                        if isinstance(child, dict)
                    ]
                    for child_index, child_visual_node in zip(gltf_children, visual_children):
                        apply_visual_metadata(child_index, child_visual_node)

                scene_roots = []
                scene_id = gltf_json.get("scene")
                scenes = gltf_json.get("scenes")
                if isinstance(scene_id, int) and isinstance(scenes, list) and 0 <= scene_id < len(scenes):
                    scene = scenes[scene_id]
                    if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
                        scene_roots = [index for index in scene["nodes"] if isinstance(index, int)]
                if not scene_roots:
                    referenced = {
                        child_index
                        for node in nodes
                        if isinstance(node, dict)
                        for child_index in (node.get("children") or [])
                        if isinstance(child_index, int)
                    }
                    scene_roots = [index for index in range(len(nodes)) if index not in referenced]

                if len(scene_roots) == 1:
                    apply_visual_metadata(scene_roots[0], visual_metadata)
                else:
                    visual_children = [
                        child
                        for child in (visual_metadata.get("children") or [])
                        if isinstance(child, dict)
                    ] if isinstance(visual_metadata, dict) else []
                    for root_index, child_visual_node in zip(scene_roots, visual_children):
                        apply_visual_metadata(root_index, child_visual_node)

                return changed

            def patch_glb_metadata(glb_path, name_mapping, color_mapping):
                with open(glb_path, "rb") as f:
                    data = f.read()

                magic, version, length = struct.unpack("<4sII", data[:12])
                if magic != b"glTF":
                    return

                chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
                if chunk_type != b"JSON":
                    return

                json_data = data[20:20 + chunk_len].decode("utf-8")
                gltf_json = json.loads(json_data)

                changed = patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_tree)

                if not changed:
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
                new_data.extend(data[20 + chunk_len:])

                with open(glb_path, "wb") as f:
                    f.write(new_data)

            def patch_gltf_metadata(gltf_path, name_mapping, color_mapping):
                with open(gltf_path, "r", encoding="utf-8") as f:
                    gltf_json = json.load(f)

                changed = patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_tree)
                if not changed:
                    return

                with open(gltf_path, "w", encoding="utf-8") as f:
                    json.dump(gltf_json, f, separators=(",", ":"))

            if export_format == "glb":
                patch_glb_metadata(str(output_path), tag_to_name, tag_to_color)
                end_phase("patch")
                from tertius_glb_runtime import deduplicate_glb_file  # copied in by the sandbox

                deduplicate_glb_file(output_path)
                end_phase("dedup")
            else:
                patch_gltf_metadata(str(output_path), tag_to_name, tag_to_color)
                end_phase("patch")
        except Exception as patch_e:
            print("Failed to patch GLTF metadata:", patch_e)
