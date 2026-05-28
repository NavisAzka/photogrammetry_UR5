import bpy
import sys
import os

argv = sys.argv

argv = argv[argv.index("--") + 1:]

obj_path = argv[0]
glb_path = argv[1]

# Reset scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import OBJ
bpy.ops.wm.obj_import(filepath=obj_path)

# Export GLB
bpy.ops.export_scene.gltf(
    filepath=glb_path,
    export_format='GLB'
)

print(f"[SUCCESS] Exported GLB: {glb_path}")